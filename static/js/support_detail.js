document.addEventListener('DOMContentLoaded', () => {
  const mapEl = document.getElementById('mini-map');
  if (!mapEl) return;
  mapEl.style.width = '100%';
  mapEl.style.height = '200px';
  if (typeof L === 'undefined') {
    console.error('Leaflet n’est pas chargé.');
    return;
  }
  const miniMap = L.map('mini-map', { zoomControl:false, dragging:false, scrollWheelZoom:false });
  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    attribution:'© CARTO', subdomains:'abcd'
  }).addTo(miniMap);
  const lat = parseFloat('{{ support.latitude }}'.replace(',', '.'));
  const lng = parseFloat('{{ support.longitude }}'.replace(',', '.'));
  const color = '{{ support.get_etat_color }}' || 'var(--color-primary)';
  const icon = L.divIcon({
    html: `<div style="width:16px;height:16px;background:${color};border-radius:50%;border:3px solid white;box-shadow:0 0 0 4px ${color}44"></div>`,
    iconSize:[16,16], iconAnchor:[8,8], className:''
  });
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
    miniMap.setView([12.3714, -1.5197], 3);
  } else {
    L.marker([lat,lng],{icon}).addTo(miniMap);
    miniMap.setView([lat,lng], 15);
  }
  miniMap.invalidateSize(true);
  setTimeout(() => miniMap.invalidateSize(true), 250);
  miniMap.on('click', () => {
    window.location.href = `{% url 'carte' %}?support={{ support.pk }}`;
  });
});

const COLORS=['#378ADD','#D85A30','#1D9E75','#7F77DD','#BA7517','#D4537E'];
let spots={{ spots_data|safe }};

function vd(){return parseInt(document.getElementById('viewdur').value);}

function fmtMin(sec){return (sec/60).toFixed(1)+' min';}

function fmtRuler(t,viewDur){
  if(viewDur<=3600){const m=Math.floor(t/60);return m===0?'0':m+'min';}
  if(viewDur<=86400){return Math.floor(t/3600)+'h';}
  if(viewDur<=604800){const d=Math.floor(t/86400);return d===0?'J1':('J'+(d+1));}
  const w=Math.floor(t/604800);return w===0?'S1':('S'+(w+1));
}

function viewLabel(v){return v<=3600?'heure':v<=86400?'jour':v<=604800?'semaine':'mois';}

function getCC(){
  const dark=window.matchMedia('(prefers-color-scheme: dark)').matches;
  return{
    text:dark?'rgba(255,255,255,0.45)':'rgba(0,0,0,0.42)',
    textPri:dark?'rgba(255,255,255,0.82)':'rgba(0,0,0,0.82)',
    rowBg:dark?'rgba(255,255,255,0.04)':'rgba(0,0,0,0.03)',
    grid:dark?'rgba(255,255,255,0.08)':'rgba(0,0,0,0.07)',
    gridMaj:dark?'rgba(255,255,255,0.14)':'rgba(0,0,0,0.12)',
  };
}

function renderMetrics(){
  const view=vd();const vl=viewLabel(view);
  const pubH=spots.reduce((a,sp)=>a+(sp.dur/sp.interval)*3600,0);
  const pubView=pubH/3600*view;
  const pct=Math.round(pubView/view*100);
  document.getElementById('metrics').innerHTML=`
    <div class="mc"><div class="ml">Spots actifs</div><div class="mv">${spots.length}</div></div>
    <div class="mc"><div class="ml">Pub totale / heure</div><div class="mv">${Math.round(pubH/60)}<span class="mu">min</span></div></div>
    <div class="mc"><div class="ml">Pub totale / ${vl}</div><div class="mv">${Math.round(pubView/60)}<span class="mu">min</span></div></div>
    <div class="mc"><div class="ml">% antenne / ${vl}</div><div class="mv">${pct}<span class="mu">%</span></div></div>
  `;
}

function renderFrise(){
  if(!spots.length){
    document.getElementById('frise-note').textContent='Aucun spot actif sur ce support.';
    const canvas=document.getElementById('frise-canvas');
    canvas.width=0;canvas.height=0;
    return;
  }
  const canvas=document.getElementById('frise-canvas');
  const container=document.getElementById('frise-container');
  const viewDur=vd();
  const dpr=Math.min(window.devicePixelRatio||1,2);
  const W=Math.max(container.clientWidth||700,300);
  const LPAD=74;const chartW=W-LPAD;
  const ROW_H=26;const RULER_H=22;const GAP=5;
  const H=RULER_H+GAP+spots.length*(ROW_H+GAP);

  canvas.width=W*dpr;canvas.height=H*dpr;
  canvas.style.width=W+'px';canvas.style.height=H+'px';
  const ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);ctx.clearRect(0,0,W,H);

  const cc=getCC();
  const pxPerSec=chartW/viewDur;

  let tickStep,subTick;
  if(viewDur<=3600){tickStep=600;subTick=120;}
  else if(viewDur<=86400){tickStep=3600;subTick=1800;}
  else if(viewDur<=604800){tickStep=86400;subTick=43200;}
  else{tickStep=604800;subTick=86400;}

  ctx.lineWidth=0.5;
  for(let t=subTick;t<viewDur;t+=subTick){
    const x=LPAD+t*pxPerSec;
    ctx.strokeStyle=cc.grid;
    ctx.beginPath();ctx.moveTo(x,RULER_H);ctx.lineTo(x,H);ctx.stroke();
  }
  for(let t=0;t<=viewDur;t+=tickStep){
    const x=LPAD+t*pxPerSec;
    ctx.strokeStyle=cc.gridMaj;
    ctx.beginPath();ctx.moveTo(x,16);ctx.lineTo(x,H);ctx.stroke();
    ctx.fillStyle=cc.text;
    ctx.font=`10px sans-serif`;ctx.textAlign='center';
    ctx.fillText(fmtRuler(t,viewDur),x,12);
  }

  let hasDense=false;
  spots.forEach((sp,i)=>{
    const col=COLORS[i%COLORS.length];
    const y=RULER_H+GAP+i*(ROW_H+GAP);
    ctx.fillStyle=cc.rowBg;
    ctx.fillRect(LPAD,y,chartW,ROW_H);
    ctx.fillStyle=cc.textPri;
    ctx.font=`11px sans-serif`;ctx.textAlign='right';
    const lbl=sp.name.length>9?sp.name.slice(0,8)+'…':sp.name;
    ctx.fillText(lbl,LPAD-6,y+ROW_H/2+4);
    ctx.fillStyle=col;
    const segW=Math.max(1.5,sp.dur*pxPerSec);
    const spacing=sp.interval*pxPerSec;
    if(spacing<2){
      hasDense=true;
      ctx.globalAlpha=0.7;
      ctx.fillRect(LPAD,y+3,chartW,ROW_H-6);
      ctx.globalAlpha=1;
    } else {
      for(let t=0;t<viewDur;t+=sp.interval){
        const x=LPAD+t*pxPerSec;
        const w=Math.min(segW,LPAD+chartW-x);
        if(w>0)ctx.fillRect(x,y+3,w,ROW_H-6);
      }
    }
  });

  ctx.strokeStyle=cc.gridMaj;ctx.lineWidth=0.5;
  ctx.strokeRect(LPAD,RULER_H,chartW,H-RULER_H);

  document.getElementById('frise-note').textContent=
    hasDense?'Barre pleine = diffusions si denses qu\'elles se confondent à cette échelle.':'';
}

function render(){renderMetrics();renderFrise();}

document.getElementById('viewdur').addEventListener('change',render);

window.addEventListener('load', () => {
  render();
});
window.addEventListener('resize',()=>renderFrise());
setTimeout(render,60);
