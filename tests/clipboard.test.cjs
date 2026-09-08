const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('app/index.html','utf8');
const code=html.slice(html.indexOf('function legacyCopy('),html.indexOf("$('#copyReport').onclick="));
(async()=>{
for(const scenario of [{legacy:true,secure:false,modern:false,want:true},{legacy:false,secure:true,modern:true,want:true},{legacy:false,secure:false,modern:false,want:false},{legacy:false,secure:true,modern:false,want:false}]){
let removed=false,written='',selected=false;
const ctx={document:{activeElement:{focus(){}},body:{appendChild(){}},createElement(){return {value:'',style:{},setAttribute(){},focus(){},select(){selected=true},setSelectionRange(){},remove(){removed=true}}},execCommand(){return scenario.legacy}},window:{isSecureContext:scenario.secure},navigator:{clipboard:{async writeText(t){if(!scenario.modern)throw Error('blocked');written=t}}}};
vm.createContext(ctx);vm.runInContext(code,ctx);assert.equal(await ctx.copyText('新聞\nhttps://example.com/a'),scenario.want);assert(removed&&selected);
if(scenario.modern)assert.equal(written,'新聞\nhttps://example.com/a');
}
console.log('Clipboard fallback: 4 scenarios passed');
})();
