const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vue = require('vue');
const { webcrypto } = require('node:crypto');
const { createModuleLoader } = require('./helpers/load-typescript.cjs');

const clone = value => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  const mounted = [], disposed = [], intervals = new Map(), storage = new Map();
  const calls = { saves: [], executions: [], disposals: 0, options: 0 };
  let interval = 0;
  const strategy = { summary: { strategy_id: 'test_strategy', strategy_version: '1', package_hash: 'hash', display_name: 'Test', executable: true },
    roles: ['ground', 'focal'].map(role_key => ({ role_key, display_name: role_key, required: true, assignment_policy: 'weighted_pool' })),
    default_random_seed: 42,
    generation_parameters: [{ key: 'coverage', default: 80, minimum: 1, maximum: 100, group: 'quick', visible_for_roles: ['ground'] },
      { key: 'spacing', default: 4, minimum: 1, maximum: 20, group: 'quick' }],
    profiles: ['preset', 'pure', 'empty'].map(profile_id => ({ profile_id, strategy_id: 'test_strategy', strategy_version: '1', catalog_id: 'catalog', display_name: profile_id, generation: 1 })) };
  const assignments = Object.fromEntries(strategy.roles.map(role => [role.role_key, { enabled: true, candidates: [{ asset_id: 'oak', weight: 1, scale: { mode: 'fixed', value: 1 } }] }]));
  const details = Object.fromEntries(strategy.profiles.map(summary => [summary.profile_id, { summary, assignments,
    strategy_parameters: summary.profile_id === 'preset' ? { spacing: 8 } : summary.profile_id === 'empty' ? {} : null }]));
  const assets = ['oak', 'grass'].map((asset_id, i) => ({ asset_id, display_name: asset_id, collection_ids: [i ? 'low' : 'tall'], suggested_scale: { mode: 'fixed', value: 1 } }));
  const api = {
    configurationOptions: async () => { calls.options++; return { strategies: [strategy], catalogs: [{ catalog_id: 'catalog' }] }; },
    profileDetail: async id => clone(details[id]),
    catalogAssets: async () => ({ items: assets, collections: [{ collection_id: 'low' }, { collection_id: 'tall' }] }),
    assetThumbnailUrl: id => '/resource/' + id,
    saveProfile: async input => { calls.saves.push(clone(input)); return { ...details.preset, strategy_parameters: input.strategy_parameters }; },
    bootstrapSession: async () => ({ stage: null, selection_regions: [] }),
    commandsAfter: () => new Promise(resolve => { calls.resolveCommands = resolve; }),
    sessionSnapshot: async () => ({ stage: null }),
  };
  class Bridge {
    constructor(callbacks) { calls.sdkCallbacks = callbacks; }
    async initialize() {}
    async execute(command) { calls.executions.push(command); }
    dispose() { calls.disposals++; }
    cancelPendingCommands() { return calls.cancelWarning ?? null; }
  }
  const load = createModuleLoader({
    window: { setInterval: callback => { intervals.set(++interval, callback); return interval; }, clearInterval: id => intervals.delete(id), setTimeout },
    document: { visibilityState: 'visible' },
    crypto: webcrypto,
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
  }, {
    vue: { ...vue, onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => disposed.push(fn) },
    [path.resolve(__dirname, '../src/api.ts')]: api,
    [path.resolve(__dirname, '../src/sdk/DtsSdkBridge.ts')]: { DtsSdkBridge: Bridge },
  });
  const state = { operationActive: vue.ref(false), loading: vue.ref(false), errorMessage: vue.ref(null) };
  const { useLiveConfiguration } = load(path.join(__dirname, '../src/features/useLiveConfiguration.ts'));
  return { model: useLiveConfiguration(state), state, calls, api, details, assets, load, mounted, disposed, intervals, storage };
}

test('Plans reset parameter drafts; legacy mappings load exact strategy defaults', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  assert.deepEqual(clone(f.model.strategyParameterValues.value), { coverage: 80, spacing: 8 });
  f.model.updateStrategyParameter('spacing', 11);
  f.model.chooseProfile('pure'); await flush();
  assert.equal(f.model.strategyParameterValues.value.spacing, 4);
  f.model.chooseProfile('empty'); await flush();
  assert.deepEqual(clone(f.model.strategyParameterValues.value), { coverage: 80, spacing: 4 });
  f.model.chooseProfile('preset'); await flush();
  assert.equal(f.model.strategyParameterValues.value.spacing, 8);
});

test('Plans use server-resolved versions once and leave newly added roles disabled', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  const original = clone(f.model.options.value.strategies[0]);
  original.summary.strategy_version = '2.0.0';
  const newer = clone(original);
  newer.summary.strategy_version = '2.10.0';
  newer.summary.package_hash = 'new-hash';
  newer.roles.push({ role_key: 'accent', display_name: 'Accent', required: false, assignment_policy: 'weighted_pool' });
  for (const strategy of [original, newer])
    strategy.profiles.forEach(profile => {
      profile.strategy_version = '2.0.0';
      profile.resolved_strategy_hash = 'new-hash';
      profile.resolved_strategy_version = '2.10.0';
    });
  f.model.options.value = { ...f.model.options.value, strategies: [original, newer] };
  assert.equal(f.model.availablePlans.value.length, 3);
  await f.model.choosePlan('preset'); await flush();
  assert.equal(f.model.selectedStrategyHash.value, 'new-hash');
  assert.equal(f.model.profileRoleEnabled.value.accent, false);
  await f.model.openMapping();
  assert.deepEqual(clone(f.model.assignments.value.accent), { enabled: false, candidates: [] });
  assert.equal(f.model.buildRunConfiguration().strategy_package_hash, 'new-hash');
});

test('Multi-Catalog choices preserve independent same-ID plants, scales and one-time self-contained execution', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  const sources = ['permanent', 'temporary'].map(catalog_id => ({ catalog_id, target_type: 'dts', provider_id: 'dts.static-mesh' }));
  f.model.options.value = { ...f.model.options.value, catalogs: sources };
  const plants = sources.map((source, index) => ({
    asset_id: 'tree_01', display_name: 'Tree ' + source.catalog_id, collection_ids: [], suggested_scale: { mode: 'fixed', value: 1 },
    thumbnail_resource_id: 'thumb-' + index, source_catalog_name: source.catalog_id,
    binding_candidate: {
      asset_id: 'binding-' + source.catalog_id, display_name_snapshot: 'Tree ' + source.catalog_id,
      weight: 1, scale: { mode: 'fixed', value: 1 }, reference_radius_m: index + 2,
      target_asset_ref: { target_type: 'dts', provider_id: 'dts.static-mesh', locator: { model_name: 'Model' + index } },
      catalog_link: { catalog_id: source.catalog_id, asset_id: 'tree_01', observed_target_hash: 'hash', observed_metadata_hash: 'hash' },
    },
  }));
  f.api.catalogAssets = async source => ({ items: [plants[sources.findIndex(item => item.catalog_id === source.catalog_id)]], collections: [] });
  f.model.chooseProfile('');
  await f.model.openMapping();
  f.model.assignments.value = { ground: { enabled: true, candidates: [] }, focal: { enabled: false, candidates: [] } };
  f.model.toggleAsset({ ...plants[0], asset_id: plants[0].binding_candidate.asset_id });
  f.model.updateCandidate('binding-permanent', 'fixed', '80');
  f.model.toggleAsset({ ...plants[1], asset_id: plants[1].binding_candidate.asset_id });
  assert.equal(f.model.activeCandidates.value.length, 2);
  assert.equal(f.model.activeCandidates.value[0].scale.value, 0.8);
  assert.equal(f.model.assetForCandidate('binding-permanent').thumbnail_resource_id, 'thumb-0');
  assert.equal(f.model.assetForCandidate('binding-permanent').asset_id, 'binding-permanent');
  assert.equal(f.model.activeCandidates.value.length, 2);
  await f.model.applyMapping();
  const run = f.model.buildRunConfiguration();
  assert.equal(run.profile_id, undefined);
  assert.equal(run.catalog_id, undefined);
  assert.equal(run.assignments.ground.candidates.length, 2);
  assert.equal(f.calls.saves.length, 0);
  for (const dispose of f.disposed) dispose();
});

test('Editing a self-contained plan keeps its legacy IDs and survives missing sources without requiring the original Profile', async () => {
  const f = fixture();
  const candidate = { asset_id: 'oak', display_name_snapshot: 'Oak', weight: 1, scale: { mode: 'fixed', value: 1 },
    target_asset_ref: { target_type: 'dts', provider_id: 'dts.static-mesh', locator: { model_name: 'Oak' } } };
  f.details.preset.assignments = { ground: { candidates: [candidate] }, focal: { enabled: false, candidates: [] } };
  await f.model.initializeOptions(); await flush();
  await f.model.openMapping();
  f.model.options.value = { ...f.model.options.value, catalogs: [] };
  f.model.selectedCatalogKey.value = '';
  f.model.updateCandidate('oak', 'fixed', '120');
  await f.model.applyMapping();
  const run = f.model.buildRunConfiguration();
  assert.equal(run.profile_id, undefined);
  assert.equal(run.catalog_id, undefined);
  assert.equal(run.assignments.ground.candidates[0].asset_id, 'oak');
  assert.equal(run.assignments.ground.candidates[0].scale.value, 1.2);
  await f.model.beginPlanSave();
  assert.equal(f.model.editingProfile.value.profile_id, 'preset');
});

test('Manual mapping clones the selected Profile, balances weights and restores on cancel', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  await f.model.openMapping();
  assert.equal(f.model.activeCandidates.value[0].asset_id, 'oak');
  f.model.toggleAsset(f.assets[1]);
  assert.deepEqual(Array.from(f.model.activeCandidates.value, candidate => candidate.weight), [0.5, 0.5]);
  f.model.updateCandidate('oak', 'fixed', '80');
  assert.equal(f.model.activeCandidates.value[0].scale.value, 0.8);
  assert.equal(f.details.preset.assignments.ground.candidates[0].scale.value, 1);
  f.model.removeCandidate('grass');
  assert.equal(f.model.activeCandidates.value[0].weight, 1);
  f.model.cancelMapping();
  assert.equal(f.model.selectedProfile.value.profile_id, 'preset');
  assert.equal(f.model.mappingOpen.value, false);
});

test('Role visibility preserves hidden values, prevents all-off and saves only when explicitly requested', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  await f.model.openMapping();
  f.model.updateStrategyParameter('coverage', 72);
  f.model.toggleRole({ target: { checked: false } });
  assert.deepEqual(Array.from(f.model.visibleGenerationParameters.value, parameter => parameter.key), ['spacing']);
  assert.equal(f.model.strategyParameterValues.value.coverage, 72);
  f.model.activeRoleKey.value = 'focal';
  f.model.toggleRole({ target: { checked: false } });
  assert.equal(f.model.assignments.value.focal.enabled, true);
  f.model.profileSaveMode.value = 'update';
  await f.model.finishMapping();
  assert.equal(f.calls.saves[0].profile_id, 'preset');
  assert.equal(f.calls.saves[0].assignments.ground.enabled, false);
  assert.equal(f.calls.saves[0].strategy_parameters.coverage, 72);
  assert.ok(!('random_seed' in f.calls.saves[0]));
});

test('Configuration sync preserves valid temporary mappings and skips locked operations', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  await f.model.openMapping();
  await f.model.finishMapping();
  const before = clone(f.model.assignments.value);
  await f.model.refreshConfigurationOptions();
  assert.deepEqual(clone(f.model.assignments.value), before);
  const count = f.calls.options;
  f.state.operationActive.value = true;
  await f.model.refreshConfigurationOptions();
  assert.equal(f.calls.options, count);
});
test('Saving a plan outside the mapping workspace includes all parameters and cancellation restores the selection', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  f.model.updateStrategyParameter('spacing', 11);
  await f.model.beginPlanSave();
  assert.equal(f.model.profileSaveOpen.value, true);
  assert.equal(f.model.mappingOpen.value, false);
  f.model.cancelProfileSave();
  assert.equal(f.model.selectedProfile.value.profile_id, 'preset');
  assert.equal(f.model.strategyParameterValues.value.spacing, 11);
  await f.model.beginPlanSave();
  f.model.chooseProfileSaveMode('create');
  await f.model.finishMapping();
  assert.deepEqual(f.calls.saves[0].strategy_parameters, { coverage: 80, spacing: 11 });
  assert.equal(f.calls.saves[0].source_profile_id, 'preset');
  assert.equal(f.calls.saves[0].profile_id, undefined);
});
test('Temporary edits retain the source plan for explicit updates without overwriting it on apply', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  await f.model.openMapping();
  f.model.setRoleEnabled({ ground: false, focal: true });
  assert.equal(f.model.assignments.value.ground.candidates.length, 1);
  f.model.setRoleEnabled({ ground: false, focal: false });
  assert.equal(f.model.assignments.value.focal.enabled, true);
  await f.model.applyMapping();
  assert.equal(f.calls.saves.length, 0);
  const run = f.model.buildRunConfiguration();
  assert.equal(run.profile_id, 'preset');
  assert.equal(run.catalog_id, undefined);
  assert.equal(run.assignments.ground.enabled, false);
  await f.model.beginPlanSave();
  assert.equal(f.model.editingProfile.value.profile_id, 'preset');
  assert.equal(f.model.profileSaveMode.value, 'update');
  await f.model.finishMapping();
  assert.equal(f.calls.saves[0].assignments.ground.enabled, false);
});

test('The one-time apply action never saves, even after choosing a save mode and returning to edit', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  await f.model.openMapping();
  f.model.toggleAsset(f.assets[1]);
  f.model.beginProfileSave();
  assert.equal(f.model.profileSaveOpen.value, true);
  assert.equal(f.model.profileSaveMode.value, 'update');
  f.model.chooseProfileSaveMode('create');
  f.model.profileName.value = 'New planting';
  f.model.cancelProfileSave();
  assert.equal(f.model.mappingOpen.value, true);
  assert.equal(f.model.activeCandidates.value.length, 2);
  await f.model.applyMapping();
  assert.equal(f.calls.saves.length, 0);
  assert.equal(f.model.mappingOpen.value, false);
  assert.equal(f.model.selectedProfileKey.value, '');
  assert.equal(f.model.assignments.value.ground.candidates.length, 2);
});

test('Save confirmation switches between update and new, retains mode-specific names and applies the returned summary', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  await f.model.openMapping();
  f.model.beginProfileSave();
  f.model.profileName.value = 'Updated planting';
  f.model.chooseProfileSaveMode('create');
  assert.equal(f.model.profileName.value, 'preset 副本');
  f.model.profileName.value = 'New planting';
  f.model.chooseProfileSaveMode('update');
  assert.equal(f.model.profileName.value, 'Updated planting');
  f.model.chooseProfileSaveMode('create');
  assert.equal(f.model.profileName.value, 'New planting');
  const optionsCalls = f.calls.options;
  f.api.saveProfile = async request => {
    f.calls.saves.push(clone(request));
    return { ...f.details.preset, summary: { ...f.details.preset.summary, profile_id: 'new', display_name: request.display_name } };
  };
  await f.model.finishMapping();
  assert.equal(f.calls.saves[0].display_name, 'New planting');
  assert.ok(!('profile_id' in f.calls.saves[0]));
  assert.ok(!('expected_generation' in f.calls.saves[0]));
  assert.equal(f.model.selectedProfile.value.profile_id, 'new');
  assert.equal(f.model.profileSaveOpen.value, false);
  assert.equal(f.model.mappingOpen.value, false);
  assert.equal(f.calls.options, optionsCalls);
});

test('A manual draft can only create a new Profile; invalid names and failed saves leave the editor intact', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  f.model.chooseProfile('');
  await f.model.openMapping();
  for (const key of ['ground', 'focal']) { f.model.activeRoleKey.value = key; f.model.toggleAsset(f.assets[0]); }
  f.model.beginProfileSave();
  assert.equal(f.model.editingProfile.value, null);
  assert.equal(f.model.profileSaveMode.value, 'create');
  f.model.chooseProfileSaveMode('update');
  assert.equal(f.model.profileSaveMode.value, 'create');
  f.model.profileName.value = '  ';
  await f.model.finishMapping();
  assert.equal(f.calls.saves.length, 0);
  assert.match(f.model.mappingError.value, /名称/);
  f.model.profileName.value = 'Planting';
  f.api.saveProfile = async () => { throw new Error('Save failed'); };
  await f.model.finishMapping();
  assert.match(f.model.mappingError.value, /Save failed/);
  assert.equal(f.model.savingProfile.value, false);
  assert.equal(f.model.profileSaveOpen.value, true);
  assert.equal(f.model.mappingOpen.value, true);
  assert.equal(f.model.activeCandidates.value.length, 1);
});

test('A pending save cannot be submitted again or dismissed, and updates include a generation guard', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  await f.model.openMapping();
  f.model.beginProfileSave();
  let complete;
  f.api.saveProfile = input => { f.calls.saves.push(clone(input)); return new Promise(resolve => { complete = resolve; }); };
  const pending = f.model.finishMapping();
  await f.model.finishMapping();
  f.model.cancelProfileSave(); f.model.cancelMapping();
  assert.equal(f.calls.saves.length, 1);
  assert.equal(f.model.mappingOpen.value, true);
  assert.equal(f.model.profileSaveOpen.value, true);
  assert.equal(f.calls.saves[0].profile_id, 'preset');
  assert.equal(f.calls.saves[0].expected_generation, 1);
  complete({ ...f.details.preset, summary: { ...f.details.preset.summary, generation: 2 } });
  await pending;
  assert.equal(f.model.selectedProfile.value.generation, 2);
  assert.equal(f.model.mappingOpen.value, false);
});

test('Updating uses the latest Profile detail, not an older configuration-list generation', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  f.details.preset.summary = { ...f.details.preset.summary, generation: 2, display_name: 'Renamed planting' };
  await f.model.openMapping();
  assert.equal(f.model.editingProfile.value.generation, 2);
  f.model.beginProfileSave();
  assert.equal(f.model.profileName.value, 'Renamed planting');
  await f.model.finishMapping();
  assert.equal(f.calls.saves[0].expected_generation, 2);
});

test('Opening the mapping panel does not download a full catalog', async () => {
  const f = fixture();
  await f.model.initializeOptions(); await flush();
  f.api.catalogAssets = () => { throw new Error('Full catalog fetch must not run'); };
  await f.model.openMapping();
  assert.equal(f.model.mappingOpen.value, true);
  assert.equal(f.model.mappingError.value, null);
});

test('Session disposal discards a late polled command and clears owned intervals', async () => {
  const f = fixture();
  const { useLiveSession } = f.load(path.join(__dirname, '../src/features/useLiveSession.ts'));
  const session = useLiveSession(f.state.errorMessage, {
    initializeConfiguration: async () => {}, onRestored() {}, onCompleted() {},
    onRegionDrawn() {}, onDrawingFinished() {}, onRegionClicked() {},
  });
  for (const mount of f.mounted) mount();
  await flush();
  assert.equal(typeof f.calls.resolveCommands, 'function');
  for (const dispose of f.disposed) dispose();
  f.calls.resolveCommands({ items: [{ sequence: 1, kind: 'import_xml', correlation: { request_id: 'late' } }] });
  await flush();
  assert.equal(f.calls.executions.length, 0);
  assert.equal(f.calls.disposals, 1);
  assert.equal(f.intervals.size, 0);
  assert.equal(session.operationActive.value, false);
});

test('Import soft-wait activity is visible; cancelling acknowledges that native loading may continue', async () => {
  const f = fixture();
  let stage = 'delivery_pending';
  f.api.sessionSnapshot = async () => ({ stage });
  f.api.cancelOperation = async () => { stage = 'cancelled'; return { stage }; };
  f.calls.cancelWarning = 'Native import may still be running';
  const { useLiveSession } = f.load(path.join(__dirname, '../src/features/useLiveSession.ts'));
  const session = useLiveSession(f.state.errorMessage, {
    initializeConfiguration: async () => {}, onRestored() {}, onCompleted() {},
    onRegionDrawn() {}, onDrawingFinished() {}, onRegionClicked() {},
  });
  await session.refreshSnapshot();
  f.calls.sdkCallbacks.onActivity('Still waiting for DTS');
  assert.equal(session.stageLabel.value, 'Still waiting for DTS');
  stage = 'elevation_ready';
  await session.refreshSnapshot();
  assert.match(session.stageLabel.value, /整理高程结果/);
  await session.cancel();
  assert.equal(f.state.errorMessage.value, 'Native import may still be running');
});
