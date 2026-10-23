const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

const source = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/api.ts'), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

test('invalid requests describe the failed phase rather than blaming every error on mapping', async () => {
  const api = {};
  vm.runInNewContext(source, {
    exports: api,
    fetch: async () => ({ok:false, status:400, json:async()=>({error:{code:'invalid_request'}})}),
  });
  for (const [request, message] of [
    [() => api.submitElevation({}, null, [], true), /高程采样结果回传失败/],
    [() => api.submitElevationChunk({}, [0], []), /高程采样结果回传失败/],
    [() => api.submitDelivery({}, 'export', true), /XML 导入结果回传失败/],
    [() => api.beginSelection({}), /种植请求无效/],
    [() => api.catalogAssets({catalog_id:'a'}), /请求参数无效/],
  ]) {
    await assert.rejects(request, error => {
      assert.equal(error.status, 400);
      assert.equal(error.code, 'invalid_request');
      assert.match(error.message, message);
      return true;
    });
  }
});
