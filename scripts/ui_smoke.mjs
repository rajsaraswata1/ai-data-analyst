import fs from 'fs';
class N{constructor(t){this.tag=t;this.children=[];this.attrs={};this.style={};this.textContent='';this.className='';}
 setAttribute(k,v){this.attrs[k]=String(v);} getAttribute(k){return this.attrs[k];} appendChild(c){this.children.push(c);return c;}
 addEventListener(){} remove(){} replaceWith(){} querySelector(){return null;} classList={add(){},remove(){}}; scrollIntoView(){}}
globalThis.document={createElement:t=>new N(t),createElementNS:(n,t)=>new N(t),createTextNode:t=>({text:String(t)}),getElementById:()=>new N('x')};
globalThis.window=globalThis; globalThis.window.addEventListener=()=>{}; globalThis.isSecureContext=false;
(0,eval)(fs.readFileSync('app/static/charts.js','utf8')); (0,eval)(fs.readFileSync('app/static/app.js','utf8'));
const results=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
let count=0,types={};
const count_nodes=(n)=>{ if(!n||!n.children) return 0; return 1+n.children.reduce((a,c)=>a+count_nodes(c),0);};
for(const r of results){ const box=window.__app.renderResult(r,'mid1',()=>{}); count++; for(const c of (r.charts||[])){const el=Charts.render(c); types[c.type]=(types[c.type]||0)+count_nodes(el);} }
console.log('rendered',count,'results; chart node counts by type:',JSON.stringify(types));
