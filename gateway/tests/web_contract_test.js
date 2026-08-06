const fs = require('fs');
const vm = require('vm');
const path = require('path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'nexus_gateway/web/app.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'nexus_gateway/web/styles.css'), 'utf8');
const html = fs.readFileSync(path.join(root, 'nexus_gateway/web/index.html'), 'utf8');
const noop = () => {};
const element = () => ({
  classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
  style: {}, dataset: {}, value: '', textContent: '', innerHTML: '', disabled: false,
  files: [], addEventListener: noop, appendChild: noop, querySelectorAll: () => [],
  click: noop, focus: noop,
});
const elements = new Map();
const document = {
  getElementById: id => elements.get(id) || elements.set(id, element()).get(id),
  querySelectorAll: () => [], createElement: () => element(), body: element(),
};
const context = {
  document,
  localStorage: { getItem: () => '', setItem: noop, removeItem: noop },
  location: { origin: 'http://example.test', reload: noop },
  console, confirm: () => true, setTimeout, clearTimeout,
  AbortController, URL, Blob, FormData, XMLHttpRequest: function () {},
  fetch: async () => ({ status: 200, ok: true, json: async () => ({}), blob: async () => new Blob() }),
};
vm.createContext(context);
vm.runInContext(source, context);

if (typeof context.initializePage !== 'function') throw Error('Setup status bootstrap missing');
if (typeof context.submitSetup !== 'function') throw Error('Setup submission missing');
if (typeof context.uploadManagedFile !== 'function') throw Error('Managed file upload missing');
if (typeof context.downloadProtectedFile !== 'function') throw Error('Protected download missing');
if (!source.includes("fetch('/api/setup/status')") || !source.includes("fetch('/api/setup'")) throw Error('Setup API contract missing');
if (!source.includes("bootstrap_token: $('setupBootstrapToken').value")) throw Error('Bootstrap token setup contract missing');
if (!source.includes("'/api/admin/files'") || !source.includes("'/api/admin/audio'") || !source.includes("'/api/admin/account'")) throw Error('Admin API contract missing');
if (!source.includes("request.open('POST', '/api/uploads')")) throw Error('Managed upload API contract missing');
if (typeof context.saveHermesConfig !== 'function') throw Error('Hermes configuration submission missing');
if (!source.includes("api('/api/admin/hermes-config')") || !source.includes("api('/api/admin/hermes-config', {") || !source.includes("method: 'PUT'")) throw Error('Hermes configuration API contract missing');
const overviewSource = source.slice(source.indexOf('async function loadOverview'), source.indexOf('async function loadHealth'));
if (overviewSource.includes('gatewayStatus') || overviewSource.includes('hermesStatus')) throw Error('Overview must not overwrite connectivity state');
const healthSource = source.slice(source.indexOf('async function loadHealth'), source.indexOf('async function loadHermesConfig'));
if (!healthSource.includes('hermes_auth_failed') || !healthSource.includes('API Key 无效')) throw Error('Hermes authentication failure UI contract missing');
const hermesIds = ['hermesHealthMessage', 'hermesConfigForm', 'hermesApiUrl', 'hermesApiToken', 'hermesCurrentPassword', 'hermesKeyConfigured', 'hermesProfileRows', 'addHermesProfileButton', 'hermesConfigMessage'];
for (const id of hermesIds) if (!html.includes('id="' + id + '"')) throw Error('Hermes configuration field missing: ' + id);
if (!html.includes('地址不变时可留空') || !html.includes('修改地址时必须重填') || !html.includes('API Server Key 永远不会回显')) throw Error('Hermes API Key retention guidance missing');
if (!css.includes('.health-message') || !css.includes('.system-form') || !css.includes('.hermes-config-panel')) throw Error('Hermes configuration styling missing');
if (typeof context.addHermesProfile !== 'function' || typeof context.renderHermesProfiles !== 'function') throw Error('Hermes profile editor logic missing');
if (!source.includes('profiles: hermesProfiles.map') || !source.includes('data-profile-field="hermes_api_url"')) throw Error('Hermes profile persistence contract missing');
if (!html.includes('/p/profile-name') || !css.includes('.profile-row') || !css.includes('.profile-config')) throw Error('Hermes profile setup guidance or styling missing');

if (source.includes('/api/admin/tls') || source.includes('uploadTlsCertificate') || source.includes('loadTlsStatus')) throw Error('Removed TLS admin code is still present');
if (html.includes('tlsCertificateFile') || html.includes('tlsPrivateKeyFile') || html.includes('nexus-local-ca.crt')) throw Error('Removed TLS certificate UI is still present');
if (!html.includes('HTTP 源站') || !html.includes('反向代理') || !html.includes('不会强制跳转 HTTPS')) throw Error('HTTP origin and reverse proxy guidance missing');
if (!css.includes('.proxy-content') || !css.includes('.app-shell') || !css.includes('.metrics-grid')) throw Error('Admin layout styling missing');

if (html.includes('127.0.0.1:18787')) throw Error('Active Web UI must not hard-code the local product-test port');
if (!html.includes('id="proxyTarget"') || !source.includes('function suggestedProxyTarget()') || !source.includes("current.protocol === 'http:'") || !source.includes("return 'http://127.0.0.1:8787'")) throw Error('Dynamic reverse-proxy target guidance missing');
context.location.origin = 'http://nas.test:8787';
if (context.suggestedProxyTarget() !== 'http://127.0.0.1:8787') throw Error('fnOS proxy target must follow the active HTTP origin port');
context.location.origin = 'http://nas.test:18787';
if (context.suggestedProxyTarget() !== 'http://127.0.0.1:18787') throw Error('Local product-test proxy target must follow the active HTTP origin port');
context.location.origin = 'https://nexus.example';
if (context.suggestedProxyTarget() !== 'http://127.0.0.1:8787') throw Error('HTTPS reverse proxy target must fall back to the packaged fnOS origin port');

const forbiddenHtml = ['data-page="chat"', 'id="chat"', '网页聊天', 'id="chatForm"', 'id="sessionRows"'];
for (const marker of forbiddenHtml) if (html.includes(marker)) throw Error('Web chat UI is still present: ' + marker);
const forbiddenSource = ['createSession', 'renameSession', 'deleteSession', 'sendChat', 'renderMarkdown', 'uploadAttachment', '/api/sessions', '/chat/stream'];
for (const marker of forbiddenSource) if (source.includes(marker)) throw Error('Web chat logic is still present: ' + marker);
const forbiddenCss = ['.chat-layout', '.chat-pane', '.composer', '.conversation-list', '.messages'];
for (const marker of forbiddenCss) if (css.includes(marker)) throw Error('Web chat styling is still present: ' + marker);

if (!html.includes('文件管理') || !html.includes('语音管理') || !html.includes('账号安全') || !html.includes('系统状态')) throw Error('Required admin sections missing');
if (!css.includes('@media (max-width: 760px)')) throw Error('Responsive admin layout missing');
if (!css.includes('grid-template-columns: minmax(0, 1fr)') || !css.includes('overflow-x: auto') || !css.includes('min-width: 0')) throw Error('Mobile navigation overflow guard missing');
console.log('WEB_CONTRACT=PASS');
