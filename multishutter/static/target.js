'use strict';
const canvas=document.getElementById('pattern'), ctx=canvas.getContext('2d',{alpha:false});
const startButton=document.getElementById('start'), statusNode=document.getElementById('status');
let intervals=[], previous=0, ownJob=null, analysisJob=null, startPending=false;
function crc8(bytes){let crc=0;for(const byte of bytes){crc^=byte;for(let i=0;i<8;i++)crc=((crc<<1)^((crc&128)?7:0))&255;}return crc;}
function bitsFor(counter){const gray=counter^(counter>>>1), payload=[165,gray>>>8,gray&255], crc=crc8(payload);const word=((165<<24)|(gray<<8)|crc)>>>0;const bits=Array.from({length:32},(_,i)=>(word>>>(31-i))&1);return bits.concat(bits);}
function resize(){const ratio=Math.min(2,window.devicePixelRatio||1);canvas.width=Math.round(innerWidth*ratio);canvas.height=Math.round(innerHeight*ratio);}
function frame(at){
  if(previous && at-previous<150)intervals.push(at-previous);previous=at;intervals=intervals.slice(-90);
  const w=canvas.width,h=canvas.height,size=Math.min(w,h)*.81,x=(w-size)/2,y=(h-size)/2;
  ctx.fillStyle='#111';ctx.fillRect(0,0,w,h);ctx.fillStyle='#080808';ctx.fillRect(x,y,size,size);
  const markers=[[.06,.06,'#ff00ff'],[.94,.06,'#00ffff'],[.94,.94,'#ffff00'],[.06,.94,'#00ff00']];
  for(const [mx,my,color] of markers){ctx.fillStyle=color;ctx.fillRect(x+(mx-.04)*size,y+(my-.04)*size,.08*size,.08*size);}
  const counter=Math.floor(performance.now()/10)&65535, bits=bitsFor(counter);
  for(let row=0;row<8;row++)for(let col=0;col<8;col++){ctx.fillStyle=bits[row*8+col]?'#ffffff':'#000000';ctx.fillRect(x+(.16+col*.085)*size,y+(.16+row*.085)*size,.085*size+1,.085*size+1);}
  requestAnimationFrame(frame);
}
async function api(path,body){const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-MultiShutter':'1'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));return data;}
async function heartbeat(){
  const sorted=[...intervals].sort((a,b)=>a-b), refresh=sorted.length?sorted[Math.floor(sorted.length/2)]:16.67;
  document.getElementById('frequency').textContent=`${(1000/refresh).toFixed(0)} Hz · ca. ${refresh.toFixed(1)} ms Raster`;
  try{await api('/api/calibration/target',{visible:!document.hidden,refresh_ms:Math.max(5,Math.min(100,refresh))});}catch{}
}
async function start(){
  if(startPending || ownJob || analysisJob)return;
  startPending=true;startButton.disabled=true;
  try{await heartbeat();const job=await api('/api/calibration/capture',{takes:5,countdown:5,interval:1,allow_partial:false});ownJob=job.id;statusNode.textContent='Messreihe startet in 5 Sekunden. Testmuster sichtbar lassen.';}
  catch(error){statusNode.textContent=error.message;startButton.disabled=false;}
  finally{startPending=false;}
}
async function poll(){
  if(!ownJob&&!analysisJob)return;
  try{
    const state=await api('/api/state');const job=state.jobs.find(job=>job.id===(analysisJob||ownJob));if(!job)return;
    if(job.status==='failed'){statusNode.textContent=job.error;ownJob=null;analysisJob=null;startButton.disabled=false;return;}
    if(job.status==='complete'&&ownJob){const session=job.result.session_id;ownJob=null;statusNode.textContent='Bilder werden optisch ausgewertet …';const next=await api('/api/calibration/analyze',{session_id:session});analysisJob=next.id;}
    else if(job.status==='complete'&&analysisJob){statusNode.textContent=`Messung fertig: ${job.result.valid_samples} gültige Runden. Korrektur in der Synchronisation übernehmen.`;analysisJob=null;startButton.disabled=false;}
    else statusNode.textContent=job.progress||'Messung läuft …';
  }catch(error){statusNode.textContent=error.message;}
}
window.addEventListener('resize',resize);document.getElementById('fullscreen').onclick=()=>document.fullscreenElement?document.exitFullscreen():document.documentElement.requestFullscreen();startButton.onclick=start;
document.addEventListener('keydown',event=>{if(event.key==='Enter')start();});
document.addEventListener('visibilitychange',heartbeat);resize();requestAnimationFrame(frame);heartbeat();setInterval(heartbeat,700);setInterval(poll,1000);
