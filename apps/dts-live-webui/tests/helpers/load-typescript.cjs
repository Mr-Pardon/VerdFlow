const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');

exports.createModuleLoader = (globals, replacements = {}) => {
  const cache = new Map();
  const load = filename => {
    const file = path.resolve(filename);
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} };
    cache.set(file, module);
    const source = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    }).outputText;
    const localRequire = specifier => {
      if (specifier in replacements) return replacements[specifier];
      if (!specifier.startsWith('.')) return require(specifier);
      const target = path.resolve(path.dirname(file), specifier + '.ts');
      if (target in replacements) return replacements[target];
      return load(target);
    };
    vm.runInNewContext(source, { ...globals, module, exports: module.exports, require: localRequire }, { filename: file });
    return module.exports;
  };
  return load;
};
