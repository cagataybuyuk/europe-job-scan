// Executes the deployed .gs source in V8 with in-memory Google service doubles.
// No source rewriting, outbound requests, real Sheet writes, or real Drive access.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const readline = require('node:readline');
const ROOT = path.resolve(__dirname, '../..');
const SHA = 'a'.repeat(40);
const SECRET = 'gd004-test-only-hmac-value';
const TEST_ID = '17dBVTbUQrjpWN5eyQkIfvwyzmMDFyYhOm1Ivxcge8nM';
const FILE_ID = '18N7HF97yIqGqYYjm0RXQdKnGrH3CHhcH';
const FOLDER_ID = '1SWD9fGmmTkhpg-dezvBGzn5h3QxRAn6U';
const canonical = value => {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k)+':'+canonical(value[k])).join(',') + '}';
  return JSON.stringify(value);
};
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
function signed({operation='health', executionId='protocol:test', payload={}, requestId='request:test', nonce, createdAt, secret=SECRET, ...overrides}={}) {
  const now = Date.now();
  const unsigned = {contract_version:'EJS-GH-EXEC-0.1', request_id:requestId,
    execution_id:executionId, operation, created_at:createdAt || new Date(now).toISOString(),
    expires_at:new Date(now+180000).toISOString(), nonce:nonce || crypto.randomBytes(12).toString('hex'),
    source_sha:SHA, environment:'TEST', capability_flags:{browser_read:true, form_value_write:false,
      approved_file_upload:false, final_submit:false}, payload, payload_hash:hash(canonical(payload)), ...overrides};
  return {...unsigned, signature:crypto.createHmac('sha256',secret).update(canonical(unsigned)).digest('hex')};
}

function createHarness(options={}) {
  const rows=[];
  const props=new Map([['EJS_HMAC_SHARED_SECRET_TEST', SECRET]]);
  const state={rows, props, writes:0, reads:0, fileReads:0, logs:[], lock:false,
    bytes:fs.readFileSync(path.join(ROOT,'tests/fixtures/browser/gd004_synthetic_cv.pdf')),
    filename:'gd004_synthetic_cv.pdf', folder:FOLDER_ID, mime:'application/pdf',
    spreadsheetTitle:'TEST_EU_Job_Tracker', sheetExists:false, ...options};
  const copy=x=>JSON.parse(JSON.stringify(x));
  const sheet={getLastRow:()=>rows.length,
    setFrozenRows:()=>{},
    getRange:(row,col,height,width)=>({
      getValues:()=>{state.reads++;return Array.from({length:height},(_,i)=>Array.from({length:width},(_,j)=>(rows[row-1+i]||[])[col-1+j]??''));},
      setValues:values=>{state.writes++;values.forEach((r,i)=>{rows[row-1+i] ||= [];r.forEach((v,j)=>{rows[row-1+i][col-1+j]=copy(v);});});if(state.corruptReadback)rows[row-1][2]='CORRUPT';}
    })};
  const ss={getId:()=>TEST_ID,getName:()=>state.spreadsheetTitle,
    getSheetByName:name=>name==='GD004 TEST Execution Queue'&&state.sheetExists?sheet:null,
    insertSheet:name=>{if(name!=='GD004 TEST Execution Queue')throw Error('Unexpected sheet');state.sheetExists=true;return sheet;}};
  const context=vm.createContext({console:{log:x=>state.logs.push(x)},
    PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k)||null,setProperty:(k,v)=>props.set(k,v),deleteProperty:k=>props.delete(k),getProperties:()=>Object.fromEntries(props)})},
    LockService:{getScriptLock:()=>({waitLock:()=>{if(state.lock)throw Error('LOCK_BUSY');state.lock=true;},releaseLock:()=>{state.lock=false;}})},
    SpreadsheetApp:{openById:id=>{if(id!==TEST_ID)throw Error('Unexpected/PROD sheet');return ss;},flush:()=>{}},
    DriveApp:{getFileById:id=>{state.fileReads++;if(id!==FILE_ID)throw Error('Unexpected file');let used=false;return {
      getName:()=>state.filename,getMimeType:()=>state.mime,getSize:()=>state.bytes.length,isTrashed:()=>false,
      getParents:()=>({hasNext:()=>!used,next:()=>{used=true;return {getId:()=>state.folder};}}),
      getBlob:()=>({getContentType:()=>state.mime,getBytes:()=>[...state.bytes]})};}},
    ContentService:{MimeType:{JSON:'application/json'},createTextOutput:text=>({text,setMimeType(){return this;}})},
    Utilities:{DigestAlgorithm:{SHA_256:'sha256'},Charset:{UTF_8:'utf8'},
      computeDigest:(alg,value)=>[...crypto.createHash('sha256').update(Array.isArray(value)?Buffer.from(value):String(value),'utf8').digest()],
      computeHmacSha256Signature:(value,secret)=>[...crypto.createHmac('sha256',secret).update(value,'utf8').digest()],
      base64Encode:bytes=>Buffer.from(bytes).toString('base64')}
  });
  vm.runInContext(`const EJS_GH_DEPLOYED_SOURCE_SHA_V1 = '${SHA}';`,context);
  for(const file of fs.readdirSync(path.join(ROOT,'google_native/apps_script_zero_cost')).filter(f=>f.endsWith('.gs'))){
    vm.runInContext(fs.readFileSync(path.join(ROOT,'google_native/apps_script_zero_cost',file),'utf8'),context,{filename:file});
  }
  function initialize(){context.ejsGhInitializeSyntheticTestV1();return JSON.parse(state.logs.at(-1));}
  function post(request){return JSON.parse(context.doPost({postData:{contents:JSON.stringify(request)}}).text);}
  return {state,context,initialize,post,sign:signed,sha:SHA,secret:SECRET,canonical,hash};
}
module.exports={createHarness,signed,canonical,hash,SHA,SECRET};

if(require.main===module){
  const h=createHarness();h.initialize();
  readline.createInterface({input:process.stdin}).on('line',line=>{
    try{const m=JSON.parse(line);const result=m.action==='snapshot'?h.state.rows:m.action==='alter_bytes'?(h.state.bytes=Buffer.from('%PDF-incorrect'),{ok:true}):h.post(m.request);
      process.stdout.write(JSON.stringify(result)+'\n');
    }catch(error){process.stdout.write(JSON.stringify({harness_error:String(error)})+'\n');}
  });
}
