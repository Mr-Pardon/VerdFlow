import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import ts from "typescript";
import { createRenderer, h, nextTick, ref } from "vue";

const require = createRequire(import.meta.url);
const { parse, compileScript } = require("vue/compiler-sfc");
const source = readFileSync(new URL("../src/components/DismissibleNotice.vue", import.meta.url), "utf8");
const { descriptor } = parse(source);
const script = compileScript(descriptor, { id: "notice-test", inlineTemplate: true });
const compiled = ts.transpileModule(script.content, { compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
} }).outputText;
const module = { exports: {} };
vm.runInNewContext(compiled, { module, exports: module.exports, require, console });
const Notice = module.exports.default;
const node = (type, text = "") => ({ type, text, children: [], props: {}, parent: null });
const renderer = createRenderer({
  createElement: type => node(type), createText: text => node("text", text), createComment: text => node("comment", text),
  setText: (item, text) => { item.text = text; },
  setElementText: (item, text) => { item.text = text; item.children = []; },
  patchProp: (item, key, previous, value) => { item.props[key] = value; },
  insert(item, parent, anchor) {
    if (item.parent) item.parent.children.splice(item.parent.children.indexOf(item), 1);
    item.parent = parent;
    const index = anchor ? parent.children.indexOf(anchor) : -1;
    parent.children.splice(index < 0 ? parent.children.length : index, 0, item);
  },
  remove(item) { if (item.parent) item.parent.children.splice(item.parent.children.indexOf(item), 1); item.parent = null; },
  parentNode: item => item.parent,
  nextSibling(item) { return item.parent?.children[item.parent.children.indexOf(item) + 1] ?? null; },
});
const walk = item => [item, ...item.children.flatMap(walk)];

test("closing a notice changes presentation only; a different operation or new error is shown again", async () => {
  const message = ref("区域高程不匹配，请检查坐标"), noticeKey = ref("job-1");
  const root = node("root");
  let dismissals = 0;
  const app = renderer.createApp({ setup: () => () => h(Notice, {
    message: message.value, noticeKey: noticeKey.value, onDismiss: () => dismissals++,
  }) });
  app.mount(root);
  const close = () => walk(root).find(item => item.props["aria-label"] === "关闭提示");
  const alert = () => walk(root).find(item => item.props.role === "alert");
  assert.ok(alert());
  close().props.onClick();
  await nextTick();
  assert.equal(alert(), undefined);
  assert.equal(message.value, "区域高程不匹配，请检查坐标");
  assert.equal(dismissals, 1);
  noticeKey.value = "job-2";
  await nextTick();
  await nextTick();
  assert.ok(alert());
  close().props.onClick();
  await nextTick();
  message.value = null;
  await nextTick();
  message.value = "区域高程不匹配，请检查坐标";
  await nextTick();
  await nextTick();
  assert.ok(alert());
  app.unmount();
});
