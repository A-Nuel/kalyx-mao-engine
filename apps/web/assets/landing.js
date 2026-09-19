(()=>{const root=document.documentElement,hero=document.querySelector('.hero'),glow=document.querySelector('.cursor-glow'),reduced=matchMedia('(prefers-reduced-motion:reduce)').matches;
if(glow&&!reduced)addEventListener('pointermove',e=>{glow.style.left=e.clientX+'px';glow.style.top=e.clientY+'px'},{passive:true});
let ticking=false;function update(){const p=hero?Math.min(1,Math.max(0,scrollY/Math.max(1,hero.offsetHeight))):0;root.style.setProperty('--hero-scroll',p.toFixed(3));ticking=false}
addEventListener('scroll',()=>{if(!ticking){requestAnimationFrame(update);ticking=true}},{passive:true});update();
if(!reduced&&'IntersectionObserver'in window){const io=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting)e.target.classList.add('in-view')}),{threshold:.18});document.querySelectorAll('.primitive article,.market,.trust-stack>div,.resource-flow>div,.telemetry').forEach(x=>io.observe(x))}
})();