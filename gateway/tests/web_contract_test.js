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
  click: noop, showModal: noop, focus: noop,
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
  window: { open: noop }, navigator: { clipboard: { writeText: noop } },
  console, confirm: () => true, setTimeout, clearTimeout, TextDecoder,
  AbortController, URL, Blob, FormData, XMLHttpRequest: function () {},
  fetch: async () => ({ status: 200, ok: true, json: async () => ({}), body: { getReader: () => ({ read: async () => ({ done: true }) }) } }),
};
vm.createContext(context);
vm.runInContext(source, context);

const markdown = context.renderMarkdown('| 名称 | 状态 |\n| --- | --- |\n| 网关 | 正常 |');
if (!markdown.includes('<table>') || !markdown.includes('<th>名称</th>') || !markdown.includes('<td>正常</td>')) throw Error('Markdown table rendering missing');
if (typeof context.initializePage !== 'function') throw Error('Setup status bootstrap missing');
if (typeof context.submitSetup !== 'function') throw Error('Setup submission missing');
if (!source.includes("fetch('/api/setup/status')") || !source.includes("fetch('/api/setup'")) throw Error('Setup API contract missing');
if (!source.includes("bootstrap_token:$('setupBootstrapToken').value")) throw Error('Bootstrap token setup contract missing');

if (typeof context.loadTlsStatus !== 'function') throw Error('TLS status loader missing');
if (typeof context.uploadTlsCertificate !== 'function') throw Error('TLS certificate uploader missing');
if (!source.includes("api('/api/admin/tls')") || !source.includes("api('/api/admin/tls',{method:'PUT'")) throw Error('TLS admin API contract missing');
if (!html.includes('id="tlsCertificateFile"') || !html.includes('id="tlsPrivateKeyFile"') || !html.includes('id="tlsFingerprint"')) throw Error('TLS certificate management UI missing');
if (!html.includes('id="tlsCaDownload"') || !html.includes('\u73b0\u5728\u65e0\u9700\u63d0\u4f9b\u6b63\u5f0f\u8bc1\u4e66') || !html.includes('\u70ed\u66ff\u6362')) throw Error('TLS transition guidance missing');
if (!source.includes('x.ca_download_available') || !source.includes('\u6b63\u5f0f\u8bc1\u4e66\u6a21\u5f0f\u4e0b\u4e0d\u4f7f\u7528\u4e34\u65f6 CA')) throw Error('TLS CA availability state missing');
if (/localStorage\.setItem\([^\n]*(certificate_chain|private_key|pem)/i.test(source)) throw Error('TLS material must not be stored in localStorage');
if (!source.includes("$('tlsCertificateFile').value='';$('tlsPrivateKeyFile').value=''")) throw Error('TLS file inputs are not cleared after upload');
vm.runInContext(`
  sessions = [
    { id: 'normal', title: 'Normal', source: 'api_server', last_active: 1 },
    { id: 'cron-lower', title: 'Scheduled lower', source: 'cron', last_active: 3 },
    { id: 'cron-upper', title: 'Scheduled upper', source: 'CRON', last_active: 2 },
  ];
  sessionFilter = 'active';
  archivedSessions = new Set();
  pinnedSessions = new Set();
`, context);
const visibleSessionIds = context.visibleSessions().map(session => session.id);
if (visibleSessionIds.join(',') !== 'normal') throw Error('Scheduled-task sessions must be hidden from the conversation list');
if (typeof context.copyMessage !== 'function') throw Error('Message copy action missing');
if (typeof context.handleComposerKeydown !== 'function') throw Error('Composer keyboard handler missing');
if (!/\.conversation-rows\{[^}]*flex:1[^}]*min-height:0[^}]*overflow(?:-y)?:auto/s.test(css)) throw Error('Conversation list is not an internal scroll region');
if (!/\.chat-pane\{[^}]*min-height:0/s.test(css) || !/\.messages\{[^}]*min-height:0/s.test(css)) throw Error('Chat pane height containment missing');
if (!/\.main\.chat-mode\{[^}]*display:flex[^}]*overflow:hidden/s.test(css) || !/\.main\.chat-mode #chat\{[^}]*flex:1[^}]*min-height:0/s.test(css)) throw Error('Chat workspace does not consume remaining viewport height');
if (!source.includes("classList.toggle('chat-mode'")) throw Error('Chat page does not enable viewport mode');
if (!/\.composer \.button\{[^}]*(?:min-width:[^;}]+;[^}]*white-space:nowrap|white-space:nowrap[^}]*min-width:)/s.test(css)) throw Error('Composer buttons can wrap');
console.log('WEB_CONTRACT=PASS');
