import assert from 'node:assert/strict';
import { loadSourceCatalogue, retainSourceSelection } from '../src/sourceSelection.ts';
const sources = [{id:'a',is_active:true},{id:'b',is_active:true},{id:'off',is_active:false}];
assert.deepEqual(retainSourceSelection([], sources), []);
assert.deepEqual(retainSourceSelection(['a'], sources), ['a']);
assert.deepEqual(retainSourceSelection(['old-test'], sources), []);
assert.deepEqual(retainSourceSelection(['a','off','old-test'], sources), ['a']);
assert.deepEqual(retainSourceSelection([], sources), []); // clear-all remains empty on next poll
const calls = [];
const items = await loadSourceCatalogue(async page => {
  calls.push(page);
  return {items:[{id:`page-${page}`}],meta:{page,pages:3}};
});
assert.deepEqual(calls,[1,2,3]);
assert.deepEqual(items.map(x=>x.id), ['page-1','page-2','page-3']);
assert.deepEqual(await loadSourceCatalogue(async()=>({items:[],meta:{page:1,pages:0}})),[]);
await assert.rejects(loadSourceCatalogue(async page => {
  if (page === 2) throw new Error('page failed');
  return {items:[{id:'a'}],meta:{page,pages:2}};
}), /page failed/); // never replace a full catalogue with a partial successful page
console.log('9 source selection assertions passed');
