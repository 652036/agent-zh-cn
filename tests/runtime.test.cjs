const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Minimal DOM fixture: no browser or third-party packages are needed in CI.
class Element {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase(); this.nodeType = 1; this.attrs = {...attrs};
    this.parentElement = null; this.children = []; this.isConnected = true;
  }
  append(child) { child.parentElement = this; this.children.push(child); return child; }
  getAttribute(key) { return this.attrs[key] ?? null; }
  setAttribute(key, value) { this.attrs[key] = value; }
  closest(selector) {
    const matches = (el, item) => {
      item = item.trim();
      if (item.startsWith('.')) return (el.attrs.class || '').split(/\s+/).includes(item.slice(1));
      if (item.startsWith('[')) {
        const match = item.match(/^\[([\w-]+)(?:='([^']*)')?\]$/);
        return match && (match[2] === undefined ? Object.hasOwn(el.attrs, match[1]) : el.attrs[match[1]] === match[2]);
      }
      return el.tagName === item.toUpperCase();
    };
    for (let el = this; el; el = el.parentElement) if (selector.split(',').some(s => matches(el, s))) return el;
    return null;
  }
  querySelectorAll() {
    const result = [];
    function visit(el) { for (const child of el.children || []) if (child.nodeType === 1) { result.push(child); visit(child); } }
    visit(this); return result;
  }
}
const element = (parent, tag, attrs) => parent.append(new Element(tag, attrs));
const text = (parent, value) => parent.append({nodeType: 3, nodeValue: value, isConnected: true});
const html = new Element('html'), body = element(html, 'body');
const header = element(body, 'header');
const agentLabel = text(element(header, 'button'), 'Agent');
const spaceLabel = text(element(body, 'h1'), 'New Space');
const tooltip = element(body, 'button', {title: 'Choose folder…', 'data-command': 'chooseFolder'});
const tip = text(element(body, 'div'), 'Tip: New space (Ctrl+N)');
const prototypeKey = text(element(body, 'button'), 'constructor');
const code = text(element(body, 'pre'), 'New Space');
const terminal = text(element(body, 'div', {class: 'xterm'}), 'Settings');
const resource = text(element(body, 'div', {class: 'monaco-icon-label'}), 'Choose a folder');
const chat = text(element(body, 'div', {class: 'markdown-body'}), 'New Space');
const userMessage = text(element(body, 'div', {'data-message-id': '123'}), 'View all');
const composer = text(element(body, 'div', {contenteditable: 'true'}), 'Agent');
// Reproduce Devin's actual transcript, session-name and composer containers.
const protectedContent = [];
for (const attrs of [
  {'data-message-event-id': 'event-1'},
  ...['prose-main', 'prose-ds', 'prose-ds-inline', 'prose-invert',
      'agent-session-title', 'agent-session-description'].map(value => ({class: value})),
  {'data-fast-scroll-fallback': ''}
]) {
  const container = element(body, 'div', {...attrs, title: 'New Space', 'aria-label': 'View all'});
  const content = text(element(container, 'span'), 'New Space');
  protectedContent.push({container, content});
}
const editableContents = ['', 'plaintext-only'].map(value =>
  text(element(body, 'div', {contenteditable: value}), 'View all'));
const sidebarContent = element(body, 'div', {'data-slot': 'sidebar-menu-button-content'});
const sidebarTitle = element(sidebarContent, 'div', {class: 'truncate', title: 'View all'});
const sidebarTitleText = text(sidebarTitle, 'New Space');
const spaceGroup = element(body, 'div', {class: 'flex group/space-title'});
const spaceTitle = element(spaceGroup, 'span', {title: 'View all'});
const spaceTitleText = text(element(spaceTitle, 'span'), 'New Space');
const sidebarRow = element(body, 'div', {'data-slot': 'sidebar-menu-button'});
const userControls = ['button', 'a'].map(tag => {
  const control = element(sidebarRow, tag, {title: 'New Space', 'aria-label': 'View all'});
  return {control, content: text(control, 'Settings')};
});
const archiveAction = element(sidebarRow, 'button', {
  'data-slot': 'sidebar-menu-action', title: 'Archive', 'aria-label': 'Archive'
});
const archiveActionText = text(archiveAction, 'Archive');
let callback, observers = 0;
const frames = [];
const context = {
  document: {
    readyState: 'complete', documentElement: html, body,
    createTreeWalker(root, _, filter) {
      const nodes = [];
      function visit(node) {
        for (const child of node.children || []) {
          if (child.nodeType === 3 && filter.acceptNode(child) === 1) nodes.push(child);
          else if (child.nodeType === 1) visit(child);
        }
      }
      visit(root); let index = 0;
      return {currentNode: null, nextNode() { this.currentNode = nodes[index++]; return !!this.currentNode; }};
    }
  },
  Node: {TEXT_NODE: 3, ELEMENT_NODE: 1}, NodeFilter: {SHOW_TEXT: 4, FILTER_REJECT: 2, FILTER_ACCEPT: 1},
  MutationObserver: class { constructor(fn) { callback = fn; observers++; } observe() {} },
  requestAnimationFrame(fn) { frames.push(fn); }
};
vm.createContext(context);
const source = fs.readFileSync(process.argv[2] || path.join(__dirname, '../agent-zh.js'), 'utf8');
vm.runInContext(source, context);
assert.equal(agentLabel.nodeValue, '智能体');
assert.equal(spaceLabel.nodeValue, '新建空间');
assert.equal(tooltip.attrs.title, '选择文件夹…');
assert.equal(tooltip.attrs['data-command'], 'chooseFolder');
assert.equal(tip.nodeValue, '提示：新建空间 (Ctrl+N)');
assert.equal(prototypeKey.nodeValue, 'constructor');
assert.equal(code.nodeValue, 'New Space');
assert.equal(terminal.nodeValue, 'Settings');
assert.equal(resource.nodeValue, 'Choose a folder');
assert.equal(chat.nodeValue, 'New Space');
assert.equal(userMessage.nodeValue, 'View all');
assert.equal(composer.nodeValue, 'Agent');
for (const {container, content} of protectedContent) {
  assert.equal(content.nodeValue, 'New Space');
  assert.equal(container.attrs.title, 'New Space');
  assert.equal(container.attrs['aria-label'], 'View all');
}
for (const content of editableContents) assert.equal(content.nodeValue, 'View all');
assert.equal(sidebarTitleText.nodeValue, 'New Space');
assert.equal(sidebarTitle.attrs.title, 'View all');
assert.equal(spaceTitleText.nodeValue, 'New Space');
assert.equal(spaceTitle.attrs.title, 'View all');
for (const {control, content} of userControls) {
  assert.equal(content.nodeValue, 'Settings');
  assert.equal(control.attrs.title, 'New Space');
  assert.equal(control.attrs['aria-label'], 'View all');
}
assert.equal(archiveActionText.nodeValue, '归档');
assert.equal(archiveAction.attrs.title, '归档');
assert.equal(archiveAction.attrs['aria-label'], '归档');
// Streaming updates and attribute changes must retain the same content boundary.
protectedContent[0].content.nodeValue = 'View all';
protectedContent[0].container.setAttribute('title', 'Download Diagnostics');
callback([{type: 'characterData', target: protectedContent[0].content},
          {type: 'attributes', target: protectedContent[0].container}]);
frames.shift()();
assert.equal(protectedContent[0].content.nodeValue, 'View all');
assert.equal(protectedContent[0].container.attrs.title, 'Download Diagnostics');
const dynamic = element(body, 'button'); const dynamicText = text(dynamic, 'Go to sessions list');
callback([{type: 'childList', addedNodes: [dynamic]}]);
assert.equal(dynamicText.nodeValue, 'Go to sessions list');
frames.shift()();
assert.equal(dynamicText.nodeValue, '转到会话列表');
dynamicText.nodeValue = 'Download Diagnostics';
callback([{type: 'characterData', target: dynamicText}]); frames.shift()();
assert.equal(dynamicText.nodeValue, '下载诊断信息');
vm.runInContext(source, context);
assert.equal(observers, 1);
console.log('Runtime UI, dynamic updates, identifiers and excluded user content: passed');
