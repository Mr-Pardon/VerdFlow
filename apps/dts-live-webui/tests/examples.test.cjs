const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const vue = require('vue');
const { createModuleLoader } = require('./helpers/load-typescript.cjs');

function fixture() {
  const mounted=[], disposed=[], listeners=new Map();
  let cleanup=0;
  const source={ ground: { enabled:false, candidates:[{asset_id:'a',weight:1,scale:{mode:'fixed',value:1},reference_radius_m:2}] } };
  const configuration={ selectedStrategy:vue.ref({parameter_preview:{}}), selectedProfile:vue.ref({profile_id:'p'}), selectedProfileDetail:vue.ref({assignments:source}),
    assignments:vue.ref({ground:{enabled:true,candidates:[{asset_id:'new',weight:1,scale:{mode:'fixed',value:2}}]}}), mappingOpen:vue.ref(false) };
  const context={panelCollapsed:vue.ref(false),operationActive:vue.ref(false),entryMode:vue.ref('manual')};
  const scope=vue.effectScope();
  const load=createModuleLoader({window:{addEventListener:(key,fn)=>listeners.set(key,fn),removeEventListener:(key,fn)=>{if(listeners.get(key)===fn)listeners.delete(key);}}}, {
    vue:{...vue,onMounted:fn=>mounted.push(fn),onBeforeUnmount:fn=>disposed.push(fn)},
    [path.resolve(__dirname,'../src/api.ts')]:{strategyPreviewClient:{},closeStrategyExamples:async()=>{cleanup++;}},
  });
  const {useExamplePanel}=load(path.join(__dirname,'../src/features/useExamplePanel.ts'));
  const panel=scope.run(()=>useExamplePanel(configuration,context));
  mounted.forEach(fn=>fn());
  return {panel,context,configuration,listeners,get cleanup(){return cleanup;},dispose(){disposed.forEach(fn=>fn());scope.stop();}};
}

test('Preview controls use snapshot dimensions, and stay independent of planting selection', async()=>{
  const f=fixture();
  assert.equal(f.panel.appearance.value[0].candidates[0].reference_radius_m,2);
  assert.equal(f.panel.enabled.value.ground,false);
  f.configuration.selectedProfile.value=null;
  assert.equal(f.panel.appearance.value[0].candidates[0].reference_radius_m,null);
  assert.equal(f.panel.appearance.value[0].candidates[0].scale.value,2);
  f.panel.toggle();await vue.nextTick();assert.equal(f.panel.open.value,true);
  assert.equal(f.cleanup,0);
  f.dispose();
});
test('Example and plan sidebars are exclusive; collapse, operation and mapping close previews',async()=>{
  const f=fixture();
  f.panel.toggle();await vue.nextTick();
  f.panel.planPickerOpen.value=true;await vue.nextTick();assert.equal(f.panel.open.value,false);
  f.panel.toggle();await vue.nextTick();assert.equal(f.panel.planPickerOpen.value,false);
  for(const blocker of [f.context.panelCollapsed,f.context.operationActive,f.configuration.mappingOpen]){
    blocker.value=true;await vue.nextTick();assert.equal(f.panel.open.value,false);
    f.panel.toggle();assert.equal(f.panel.open.value,false);
    blocker.value=false;await vue.nextTick();f.panel.toggle();await vue.nextTick();
  }
  f.configuration.selectedStrategy.value={};await vue.nextTick();assert.equal(f.panel.open.value,false);
  f.dispose();
});
test('Page exit and disposal cancel session previews, remove listeners and release cached geometry',async()=>{
  const f=fixture();f.panel.toggle();await vue.nextTick();
  f.panel.cache.result={points:[[1,2,0]]};
  f.listeners.get('pagehide')();await vue.nextTick();assert.equal(f.panel.open.value,false);assert.equal(f.cleanup,1);
  f.dispose();assert.equal(f.listeners.size,0);assert.equal(f.panel.cache.result,null);assert.equal(f.cleanup,2);
});
test('Adjacent sidebar positioning coalesces drag notifications and disposes all browser resources',()=>{
  const mounted=[],disposed=[],listeners=new Map(),frames=new Map();
  let reads=0,disconnected=0,id=0,mutation,resize;
  const anchor={getBoundingClientRect(){reads++;return {left:900,top:50};}};
  class Mutation {constructor(callback){mutation=callback;}observe(){}disconnect(){disconnected++;}}
  class Resize {constructor(callback){resize=callback;}observe(){}disconnect(){disconnected++;}}
  const load=createModuleLoader({MutationObserver:Mutation,ResizeObserver:Resize,window:{innerWidth:1400,innerHeight:900,requestAnimationFrame:fn=>{frames.set(++id,fn);return id;},cancelAnimationFrame:key=>frames.delete(key),addEventListener:(key,fn)=>listeners.set(key,fn),removeEventListener:key=>listeners.delete(key)}},{vue:{...vue,onMounted:fn=>mounted.push(fn),onBeforeUnmount:fn=>disposed.push(fn)}});
  const {useAdjacentPanel}=load(path.join(__dirname,'../src/features/useAdjacentPanel.ts'));
  const panel=useAdjacentPanel(()=>anchor,{width:460});mounted.forEach(fn=>fn());
  assert.equal(panel.overlay.value,false);assert.equal(panel.position.value.left,'428px');
  for(let i=0;i<30;i++){mutation();resize();}
  assert.equal(frames.size,1);assert.equal(reads,1);
  const fn=[...frames.values()][0];frames.clear();fn();assert.equal(reads,2);
  mutation();disposed.forEach(fn=>fn());assert.equal(frames.size,0);assert.equal(listeners.size,0);assert.equal(disconnected,2);
});
