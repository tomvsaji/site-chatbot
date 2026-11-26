import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
const source=await readFile(new URL('../static/app.js', import.meta.url),'utf8');
class Element {
  constructor(){this.children=[];this.classes=new Set();this.classList={add:(...v)=>v.forEach(x=>this.classes.add(x)),remove:(...v)=>v.forEach(x=>this.classes.delete(x))};}
  set textContent(value){this.text=value;this.children=[];}
  get textContent(){return this.children.length?this.children.map(c=>c.textContent).join(''):this.text||'';}
  appendChild(child){child.parent=this;this.children.push(child);return child;}
  replaceChildren(...children){this.text='';this.children=[];children.forEach(c=>this.appendChild(c));}
  querySelector(){return this.children[0];}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(c=>c!==this);}
  setAttribute(){} removeAttribute(){} addEventListener(){} focus(){}
}
async function scenario(events){
 const nodes=Object.fromEntries(['#chat-form','#question','#messages','#clear-chat','#suggestions'].map(k=>[k,new Element()]));
 nodes['#chat-form'].appendChild(new Element());let step=0;
 const pending=()=>nodes['#messages'].children.at(-1).querySelector('p');
 const reader={read:async()=>{
  if(step===events.length)return {done:true};
  const [kind,data,check]=events[step++];check?.(pending());
  return {done:false,value:new TextEncoder().encode(`event: ${kind}\ndata: ${JSON.stringify(data)}\n\n`)};
 }};
 const document={querySelector:k=>nodes[k],querySelectorAll:()=>[],createElement:()=>new Element()};
 const context=vm.createContext({document,fetch:async()=>({ok:true,body:{getReader:()=>reader}}),TextDecoder,Uint8Array});
 vm.runInContext(source+'\nglobalThis.runQuestion=sendQuestion;',context);
 await context.runQuestion('Test question');return nodes;
}
const nodes=await scenario([
 ['delta',{text:'First. '}],
 ['status',{message:'Still generating'},p=>assert.equal(p.textContent,'First. ')],
 ['delta',{text:'Second.'},p=>assert.equal(p.textContent,'First. ')],
 ['reset',{},p=>assert.equal(p.textContent,'First. Second.')],
 ['claim',{text:'Old claim'},p=>assert.equal(p.textContent,'')],
 ['reset',{},p=>assert.equal(p.textContent,'Old claim')],
 ['claim',{text:'New claim'},p=>assert.equal(p.textContent,'')],
 ['status',{message:'Still working'},p=>{assert.equal(p.textContent,'New claim');assert.equal(p.children.length,1);}],
 ['final',{answer:'Authoritative final.',sources:[]},p=>assert.equal(p.textContent,'New claim')],
]);
assert.equal(nodes['#messages'].children.at(-1).querySelector('p').textContent,'Authoritative final.');
const failed=await scenario([['delta',{text:'Provisional'}],['error',{error:'Safe failure'}]]);
assert.equal(failed['#messages'].children.at(-1).querySelector('p').textContent,'Safe failure');
assert.ok(!failed['#messages'].textContent.includes('Provisional'));
console.log('Standalone stream status, delta, claim, reset, final and error assertions passed.');
