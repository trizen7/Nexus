const fs = require('fs');
const vm = require('vm');
const path = require('path');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'nexus_gateway/web/app.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'nexus_gateway/web/styles.css'), 'utf8');
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
if (typeof context.copyMessage !== 'function') throw Error('Message copy action missing');
if (typeof context.handleComposerKeydown !== 'function') throw Error('Composer keyboard handler missing');
if (!/\.conversation-rows\{[^}]*flex:1[^}]*min-height:0[^}]*overflow(?:-y)?:auto/s.test(css)) throw Error('Conversation list is not an internal scroll region');
if (!/\.chat-pane\{[^}]*min-height:0/s.test(css) || !/\.messages\{[^}]*min-height:0/s.test(css)) throw Error('Chat pane height containment missing');
if (!/\.main\.chat-mode\{[^}]*display:flex[^}]*overflow:hidden/s.test(css) || !/\.main\.chat-mode #chat\{[^}]*flex:1[^}]*min-height:0/s.test(css)) throw Error('Chat workspace does not consume remaining viewport height');
if (!source.includes("classList.toggle('chat-mode'")) throw Error('Chat page does not enable viewport mode');
if (!/\.composer \.button\{[^}]*(?:min-width:[^;}]+;[^}]*white-space:nowrap|white-space:nowrap[^}]*min-width:)/s.test(css)) throw Error('Composer buttons can wrap');
console.log('WEB_CONTRACT=PASS');
