'use strict';

let token = localStorage.getItem('nexus_token') || '';
let files = [];
let audioFiles = [];

const $ = id => document.getElementById(id);
const titles = {
  overview: ['概览', '查看 Nexus 当前运行情况'],
  files: ['文件管理', '上传、下载和清理普通文件'],
  audio: ['语音管理', '集中查看 Android App 上传的语音'],
  account: ['账号安全', '修改网页端与 App 共用的登录账号'],
  system: ['系统状态', '检查 Gateway、Hermes 与访问入口'],
};

function authHeaders() {
  return { Authorization: 'Bearer ' + token };
}

async function api(url, options = {}) {
  const request = { ...options, headers: { ...(options.headers || {}), ...authHeaders() } };
  const response = await fetch(url, request);
  if (response.status === 401) {
    let code = '';
    try {
      code = (await response.clone().json()).error?.code || '';
    } catch (_error) {
      // Only an explicit device-token failure should clear the browser login.
    }
    if (code === 'unauthorized') {
      logout();
      throw new Error('登录已失效');
    }
  }
  return response;
}

async function initializePage() {
  try {
    const response = await fetch('/api/setup/status');
    const payload = await response.json();
    if (!payload.initialized) {
      $('setupPage').classList.remove('hidden');
      return;
    }
    if (token) showApp();
    else $('loginPage').classList.remove('hidden');
  } catch (_error) {
    $('loginPage').classList.remove('hidden');
    $('loginError').textContent = '无法连接 Nexus Gateway';
  }
}

async function submitSetup(event) {
  event.preventDefault();
  $('setupError').textContent = '';
  if ($('setupPassword').value !== $('setupPasswordConfirm').value) {
    $('setupError').textContent = '两次输入的密码不一致';
    return;
  }
  try {
    const response = await fetch('/api/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: $('setupUsername').value.trim(),
        password: $('setupPassword').value,
        hermes_api_url: $('setupHermesUrl').value.trim(),
        hermes_api_token: $('setupHermesToken').value,
        bootstrap_token: $('setupBootstrapToken').value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      $('setupError').textContent = payload.error?.message || '初始化失败';
      return;
    }
    $('setupHermesToken').value = '';
    $('setupBootstrapToken').value = '';
    $('setupPassword').value = '';
    $('setupPasswordConfirm').value = '';
    $('setupPage').classList.add('hidden');
    $('loginPage').classList.remove('hidden');
    $('username').value = payload.username || '';
    $('password').focus();
  } catch (_error) {
    $('setupError').textContent = '初始化请求失败，请检查 Gateway 状态';
  }
}

async function signIn(event) {
  event.preventDefault();
  $('loginError').textContent = '';
  try {
    const response = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: $('username').value, password: $('password').value }),
    });
    if (!response.ok) {
      $('loginError').textContent = '账号或密码错误';
      return;
    }
    const payload = await response.json();
    token = payload.access_token;
    localStorage.setItem('nexus_token', token);
    localStorage.setItem('nexus_username', payload.username);
    showApp();
  } catch (_error) {
    $('loginError').textContent = '登录请求失败，请检查网络连接';
  }
}

function suggestedProxyTarget() {
  try {
    const current = new URL(location.origin);
    if (current.protocol === 'http:') {
      return `http://127.0.0.1${current.port ? `:${current.port}` : ''}`;
    }
  } catch (_error) {
    // Fall back to the packaged fnOS origin port when the browser origin is unavailable.
  }
  return 'http://127.0.0.1:8787';
}

function showApp() {
  $('setupPage').classList.add('hidden');
  $('loginPage').classList.add('hidden');
  $('appPage').classList.remove('hidden');
  $('entryAddress').textContent = location.origin;
  $('systemEntry').textContent = location.origin;
  $('proxyTarget').textContent = suggestedProxyTarget();
  $('newUsername').value = localStorage.getItem('nexus_username') || '';
  loadAll();
}

function logout() {
  localStorage.removeItem('nexus_token');
  token = '';
  location.reload();
}

function showPage(name) {
  if (!titles[name]) return;
  document.querySelectorAll('.section').forEach(section => section.classList.toggle('active', section.id === name));
  document.querySelectorAll('.nav').forEach(item => item.classList.toggle('active', item.dataset.page === name));
  $('pageTitle').textContent = titles[name][0];
  $('pageDesc').textContent = titles[name][1];
  if (name === 'files') loadFiles();
  if (name === 'audio') loadAudio();
  if (name === 'system') Promise.allSettled([loadHealth(), loadHermesConfig()]);
}

async function loadAll() {
  await Promise.allSettled([loadOverview(), loadFiles(), loadAudio(), loadHealth(), loadHermesConfig()]);
}

async function loadOverview() {
  const response = await api('/api/admin/overview');
  if (!response.ok) throw new Error('概览读取失败');
  const payload = await response.json();
  $('metricSessions').textContent = payload.session_count ?? 0;
  $('metricFiles').textContent = payload.file_count ?? 0;
  $('metricAudio').textContent = payload.audio_count ?? 0;
  $('metricBytes').textContent = size((payload.file_bytes || 0) + (payload.audio_bytes || 0));
}

async function loadHealth() {
  try {
    const response = await fetch('/health');
    const payload = await response.json();
    const authFailed = payload.error?.code === 'hermes_auth_failed' || payload.upstream?.status === 'auth_failed';
    const hermesOk = response.ok && payload.status === 'ok' && payload.upstream?.status === 'ok';
    $('gatewayVersion').textContent = payload.version || '—';
    $('hermesVersion').textContent = payload.upstream?.version || '—';
    $('gatewayStatus').textContent = '正常';
    $('gatewayStatus').dataset.status = 'ok';
    $('hermesStatus').textContent = authFailed ? 'API Key 无效' : (hermesOk ? '正常' : '连接异常');
    $('hermesStatus').dataset.status = hermesOk ? 'ok' : 'error';
    $('hermesHealthMessage').textContent = hermesOk ? '' : (payload.error?.message || '无法连接 Hermes API');
    $('hermesHealthMessage').classList.toggle('hidden', hermesOk);
  } catch (_error) {
    $('gatewayStatus').textContent = '不可用';
    $('gatewayStatus').dataset.status = 'error';
    $('hermesStatus').textContent = '未知';
    $('hermesStatus').dataset.status = 'error';
    $('hermesHealthMessage').textContent = '无法读取 Nexus Gateway 状态';
    $('hermesHealthMessage').classList.remove('hidden');
  }
}

async function loadHermesConfig() {
  const response = await api('/api/admin/hermes-config');
  if (!response.ok) throw new Error('Hermes 配置读取失败');
  const payload = await response.json();
  $('hermesApiUrl').value = payload.hermes_api_url || '';
  $('hermesKeyConfigured').textContent = payload.key_configured ? '当前已配置 API Server Key' : '当前未配置 API Server Key';
}

async function saveHermesConfig(event) {
  event.preventDefault();
  const button = $('saveHermesConfigButton');
  const message = $('hermesConfigMessage');
  message.textContent = '';
  message.className = 'form-message';
  button.disabled = true;
  try {
    const response = await api('/api/admin/hermes-config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        current_password: $('hermesCurrentPassword').value,
        hermes_api_url: $('hermesApiUrl').value.trim(),
        hermes_api_token: $('hermesApiToken').value,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      message.textContent = payload.error?.message || 'Hermes 配置保存失败';
      message.className = 'form-message error';
      return;
    }
    $('hermesApiToken').value = '';
    $('hermesCurrentPassword').value = '';
    message.textContent = 'Hermes 连接验证通过，配置已保存';
    message.className = 'form-message success';
    await Promise.allSettled([loadHermesConfig(), loadHealth(), loadOverview()]);
  } catch (_error) {
    message.textContent = '保存请求失败，请检查 Nexus Gateway 状态';
    message.className = 'form-message error';
  } finally {
    button.disabled = false;
  }
}

async function loadFiles() {
  const response = await api('/api/admin/files');
  if (!response.ok) throw new Error('文件列表读取失败');
  files = (await response.json()).data || [];
  renderFiles();
}

async function loadAudio() {
  const response = await api('/api/admin/audio');
  if (!response.ok) throw new Error('语音列表读取失败');
  audioFiles = (await response.json()).data || [];
  renderAudio();
}

function renderFiles() {
  const query = $('search').value.trim().toLowerCase();
  const visible = files.filter(file => ((file.name || '') + ' ' + (file.mime_type || '') + ' ' + (file.date || '')).toLowerCase().includes(query));
  $('fileRows').innerHTML = visible.map(file => managedFileRow(file, 'file')).join('');
  $('fileEmpty').classList.toggle('hidden', visible.length !== 0);
}

function renderAudio() {
  const query = $('audioSearch').value.trim().toLowerCase();
  const visible = audioFiles.filter(file => ((file.name || '') + ' ' + (file.date || '')).toLowerCase().includes(query));
  $('audioRows').innerHTML = visible.map(file => managedFileRow(file, 'audio')).join('');
  $('audioEmpty').classList.toggle('hidden', visible.length !== 0);
}

function managedFileRow(file, collection) {
  const name = esc(file.name || '未命名文件');
  const encodedName = encodeURIComponent(file.name || 'download');
  return '<tr>' +
    '<td><div class="file-name"><span class="file-badge">' + fileKind(file.name) + '</span><div><b>' + name + '</b><small>' + esc(file.mime_type || '未知类型') + '</small></div></div></td>' +
    '<td>' + size(file.size) + '</td>' +
    '<td>' + esc(file.date || '—') + '</td>' +
    '<td>' + formatTimestamp(file.created_at) + '</td>' +
    '<td><div class="row-actions">' +
      '<button class="button compact secondary" data-action="download" data-url="' + escAttr(file.download_url || '') + '" data-name="' + escAttr(encodedName) + '" type="button">下载</button>' +
      '<button class="button compact danger" data-action="delete" data-id="' + escAttr(file.id || '') + '" data-collection="' + collection + '" type="button">删除</button>' +
    '</div></td>' +
  '</tr>';
}

function fileKind(name = '') {
  const extension = name.includes('.') ? name.split('.').pop().slice(0, 4).toUpperCase() : 'FILE';
  return esc(extension || 'FILE');
}

async function handleManagedFileAction(event) {
  const button = event.target.closest?.('button[data-action]');
  if (!button) return;
  if (button.dataset.action === 'download') {
    try {
      await downloadProtectedFile(button.dataset.url, button.dataset.name);
    } catch (error) {
      toast(error.message || '下载失败');
    }
    return;
  }
  if (button.dataset.action === 'delete') {
    if (button.dataset.collection === 'audio') await removeAudio(button.dataset.id);
    else await removeFile(button.dataset.id);
  }
}

function uploadManagedFile(file, onProgress = () => {}) {
  if (!file) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const form = new FormData();
    form.append('file', file, file.name);
    request.open('POST', '/api/uploads');
    request.setRequestHeader('Authorization', 'Bearer ' + token);
    request.upload.onprogress = event => {
      if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100));
    };
    request.onerror = () => reject(new Error('文件上传失败'));
    request.onload = () => {
      if (request.status === 401) {
        logout();
        reject(new Error('登录已失效'));
        return;
      }
      if (request.status < 200 || request.status >= 300) {
        reject(new Error('文件上传失败'));
        return;
      }
      try {
        resolve(JSON.parse(request.responseText).file);
      } catch (_error) {
        reject(new Error('服务器返回无效'));
      }
    };
    request.send(form);
  });
}

async function uploadFile(file) {
  if (!file) return;
  const button = $('uploadFileButton');
  button.disabled = true;
  try {
    await uploadManagedFile(file, progress => { button.textContent = '上传 ' + progress + '%'; });
    toast('文件上传成功');
    await Promise.all([loadFiles(), loadOverview()]);
  } catch (error) {
    toast(error.message || '上传失败');
  } finally {
    button.disabled = false;
    button.textContent = '上传文件';
    $('fileUploadInput').value = '';
  }
}

async function removeFile(id) {
  if (!id || !confirm('确定删除这个文件？')) return;
  const response = await api('/api/files/' + encodeURIComponent(id), { method: 'DELETE' });
  if (!response.ok) {
    toast('文件删除失败');
    return;
  }
  await Promise.all([loadFiles(), loadOverview()]);
  toast('文件已删除');
}

async function removeAudio(id) {
  if (!id || !confirm('确定删除这条语音？')) return;
  const response = await api('/api/files/' + encodeURIComponent(id), { method: 'DELETE' });
  if (!response.ok) {
    toast('语音删除失败');
    return;
  }
  await Promise.all([loadAudio(), loadOverview()]);
  toast('语音已删除');
}

async function protectedBlob(url) {
  const response = await api(url);
  if (!response.ok) throw new Error('文件读取失败');
  return response.blob();
}

async function downloadProtectedFile(url, encodedName) {
  const blob = await protectedBlob(url);
  downloadBlob(blob, decodeURIComponent(encodedName));
}

function downloadBlob(blob, name) {
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

async function changeAccount(event) {
  event.preventDefault();
  $('accountMessage').textContent = '';
  if ($('newPassword').value !== $('confirmPassword').value) {
    $('accountMessage').textContent = '两次输入的新密码不一致';
    $('accountMessage').className = 'form-message error';
    return;
  }
  const response = await api('/api/admin/account', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      current_password: $('currentPassword').value,
      username: $('newUsername').value,
      password: $('newPassword').value,
    }),
  });
  const payload = await response.json();
  if (!response.ok) {
    $('accountMessage').textContent = payload.error?.message || '修改失败';
    $('accountMessage').className = 'form-message error';
    return;
  }
  token = payload.access_token;
  localStorage.setItem('nexus_token', token);
  localStorage.setItem('nexus_username', payload.username);
  $('currentPassword').value = '';
  $('newPassword').value = '';
  $('confirmPassword').value = '';
  $('accountMessage').textContent = '修改成功，其他设备需要重新登录';
  $('accountMessage').className = 'form-message success';
}

function toast(text) {
  $('toast').textContent = text;
  $('toast').classList.remove('hidden');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $('toast').classList.add('hidden'), 2600);
}

function size(bytes) {
  const value = Number(bytes) || 0;
  if (value >= 1073741824) return (value / 1073741824).toFixed(1) + ' GB';
  if (value >= 1048576) return (value / 1048576).toFixed(1) + ' MB';
  if (value >= 1024) return (value / 1024).toFixed(1) + ' KB';
  return value + ' B';
}

function formatTimestamp(timestamp) {
  const value = Number(timestamp);
  if (!value) return '—';
  return new Date(value * 1000).toLocaleString();
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
}

function escAttr(value) {
  return esc(value).split(String.fromCharCode(10)).join(' ').split(String.fromCharCode(13)).join(' ');
}

$('setupForm').onsubmit = submitSetup;
$('loginForm').onsubmit = signIn;
$('logoutButton').onclick = logout;
$('accountForm').onsubmit = changeAccount;
$('hermesConfigForm').onsubmit = saveHermesConfig;
$('search').oninput = renderFiles;
$('audioSearch').oninput = renderAudio;
$('refreshFilesButton').onclick = loadFiles;
$('refreshAudioButton').onclick = loadAudio;
$('refreshHealthButton').onclick = loadHealth;
$('refreshOverviewButton').onclick = () => Promise.allSettled([loadOverview(), loadHealth()]);
$('uploadFileButton').onclick = () => $('fileUploadInput').click();
$('fileUploadInput').onchange = event => uploadFile(event.target.files[0]);
$('fileRows').addEventListener('click', handleManagedFileAction);
$('audioRows').addEventListener('click', handleManagedFileAction);
document.querySelectorAll('.nav').forEach(item => { item.onclick = () => showPage(item.dataset.page); });
document.querySelectorAll('[data-go-page]').forEach(item => { item.onclick = () => showPage(item.dataset.goPage); });

initializePage();
