'use strict';
const el=id=>document.getElementById(id);
let active=false, busy=false, polling=null;
async function request(path){
  const response=await fetch(path,{method:'POST',headers:{'X-Kiosk-CSRF':document.querySelector('meta[name="kiosk-csrf"]').content},credentials:'same-origin'});
  const data=await response.json();
  if(!response.ok) throw new Error(data.error||'Session unavailable. Ask the operator for help.');
  return data;
}
function display(data){
  const state=data.state;
  const screens={waiting_scan:['01 · YOUR PHONE IS NEXT','Scan. Upload. Done.','Point your phone camera at the QR code.'],waiting_upload:['02 · WAITING ON YOUR PHONE','Your phone has the wheel.','Choose your PDF and print style on your phone.'],queued:['03 · STAGING QUOTE','Ready for the print station.','Payment is skipped only in this simulation. Your document is queued.'],claimed:['04 · PREPARING','Your file is on its way.','The agent is verifying your document.'],printing:['05 · PRINTING SIMULATION','Paper, meet destiny.','The simulator is processing your job.'],completed:['06 · COMPLETE','All done.','Simulated printing completed. No physical print or payment occurred.'],needs_review:['OPERATOR HELP','Your job needs attention.','Ask the kiosk operator for help. Do not retry an uncertain print.'],cancelled:['SESSION ENDED','This job was cancelled.','Ask the kiosk operator for help.'],expired:['SESSION ENDED','Your QR has expired.','Tap Finish to return to the start screen.']};
  const screen=screens[state];if(!screen) return;
  el('step').textContent=screen[0];el('title').textContent=screen[1];el('description').textContent=screen[2];
  el('qr').hidden=state!=='waiting_scan';
  el('cancel').hidden=!['waiting_scan','waiting_upload'].includes(state);
  el('details').textContent=data.pages?`${data.pages} pages · ₹${(data.amount_paise/100).toFixed(2)} simulation quote`:'';
  el('timer').textContent=data.expires_in!==undefined?`${data.expires_in}s remaining`:'';
  el('printer').textContent=data.printer_status||'Staging simulator';
  const finished=['completed','needs_review','cancelled','expired'].includes(state);
  el('reset').hidden=!finished;if(finished){clearInterval(polling);polling=null;}
}
async function poll(){if(!active||busy)return;busy=true;try{display(await request('/status'));el('error').textContent='';}catch(error){el('error').textContent=error.message;}finally{busy=false;}}
el('start').addEventListener('click',async()=>{el('start').disabled=true;try{const data=await request('/start');active=true;el('welcome').hidden=true;el('flow').hidden=false;el('qr').src='/qr.png';display(data);polling=setInterval(poll,3000);}catch(error){el('error').textContent=error.message;}finally{el('start').disabled=false;}});
el('reset').addEventListener('click',async()=>{el('reset').disabled=true;try{await request('/reset');active=false;el('flow').hidden=true;el('welcome').hidden=false;el('qr').removeAttribute('src');el('error').textContent='';}catch(error){el('error').textContent=error.message;}finally{el('reset').disabled=false;}});
request('/status').then(data=>{if(data.state!=='idle'){active=true;el('welcome').hidden=true;el('flow').hidden=false;el('qr').src='/qr.png';display(data);if(!['completed','needs_review','cancelled','expired'].includes(data.state))polling=setInterval(poll,3000);}}).catch(error=>{el('error').textContent=error.message;});
el('cancel').addEventListener('click',async()=>{el('cancel').disabled=true;try{await request('/cancel');clearInterval(polling);polling=null;active=false;el('flow').hidden=true;el('welcome').hidden=false;el('qr').removeAttribute('src');el('error').textContent='';}catch(error){el('error').textContent=error.message;}finally{el('cancel').disabled=false;}});
