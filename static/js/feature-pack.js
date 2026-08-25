(function(){
  function csrf(){const m=document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);return m?decodeURIComponent(m[1]):''}

  function setupTypewriter(){
    const cfg=document.getElementById('delta-feature-config');
    const text=(cfg?.dataset.topText||'').trim();
    if(!text)return;
    document.querySelectorAll('.store-topbar,.mobile-social-strip').forEach(bar=>{
      bar.innerHTML='<span class="topbar-typewriter" aria-live="polite"></span>';
      const node=bar.querySelector('.topbar-typewriter');
      let i=0, timer=null;
      const draw=()=>{
        node.textContent=text.slice(0,i);
        if(i<text.length){i++;timer=setTimeout(draw,42);return}
        timer=setTimeout(()=>{i=0;node.textContent='';draw()},4200);
      };
      draw();
      document.addEventListener('visibilitychange',()=>{if(document.hidden&&timer)clearTimeout(timer)},{once:true});
    });
  }

  function setupNotifications(){
    const cfg=document.getElementById('delta-feature-config');
    const sheet=document.getElementById('notification-backdrop');
    const head=document.querySelector('.mobile-head');
    if(!cfg||!sheet||!head)return;
    const count=Math.max(0,parseInt(cfg.dataset.unread||'0',10)||0);
    const phone=head.querySelector('.mobile-phone');
    const btn=document.createElement('button');
    btn.type='button';
    btn.className='mobile-head-btn notification-bell'+(count?' has-unread':'');
    btn.setAttribute('aria-label','اطلاعیه‌ها');
    btn.innerHTML='<svg viewBox="0 0 24 24"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"></path><path d="M10 21h4"></path></svg><span class="notification-badge '+(count?'':'is-zero')+'">'+(count>99?'99+':count)+'</span>';
    if(phone)head.insertBefore(btn,phone);else head.appendChild(btn);
    const close=sheet.querySelector('.notification-close');
    const badge=btn.querySelector('.notification-badge');
    const open=async()=>{
      sheet.classList.add('open');sheet.setAttribute('aria-hidden','false');document.body.classList.add('modal-open');
      if(!badge.classList.contains('is-zero')){
        try{await fetch('/notifications/read/',{method:'POST',headers:{'X-CSRFToken':csrf(),'X-Requested-With':'XMLHttpRequest'}})}catch(_){ }
        badge.textContent='0';badge.classList.add('is-zero');btn.classList.remove('has-unread');
      }
    };
    const hide=()=>{sheet.classList.remove('open');sheet.setAttribute('aria-hidden','true');document.body.classList.remove('modal-open')};
    btn.addEventListener('click',open);if(close)close.addEventListener('click',hide);sheet.addEventListener('click',e=>{if(e.target===sheet)hide()});
  }

  function addEye(input){
    if(!input||input.dataset.eyeReady==='1')return;
    const wrap=input.closest('.auth-input');if(!wrap)return;
    input.dataset.eyeReady='1';
    const btn=document.createElement('button');btn.type='button';btn.className='password-eye';btn.setAttribute('aria-label','نمایش رمز عبور');
    const render=()=>{const visible=input.type==='text';btn.classList.toggle('is-visible',visible);btn.setAttribute('aria-label',visible?'مخفی کردن رمز عبور':'نمایش رمز عبور');btn.innerHTML=visible?'<svg viewBox="0 0 24 24"><path d="M3 3l18 18"></path><path d="M10.6 10.7a2 2 0 0 0 2.7 2.7"></path><path d="M9.9 4.3A10.5 10.5 0 0 1 12 4c5.4 0 9 6 9 8a12.8 12.8 0 0 1-2.2 3.3"></path><path d="M6.2 6.2C4.1 7.7 3 10.2 3 12c0 2 3.6 8 9 8 1.4 0 2.6-.4 3.7-1"></path></svg>':'<svg viewBox="0 0 24 24"><path d="M3 12s3.6-7 9-7 9 7 9 7-3.6 7-9 7-9-7-9-7Z"></path><circle cx="12" cy="12" r="2.6"></circle></svg>'};
    btn.addEventListener('click',()=>{const pos=input.selectionStart;input.type=input.type==='password'?'text':'password';render();input.focus();try{input.setSelectionRange(pos,pos)}catch(_){}});render();wrap.appendChild(btn);
  }
  function setupEyes(){document.querySelectorAll('.auth-input input[type="password"],.auth-input input[data-eye-ready="1"]').forEach(addEye)}

  function setupAjaxLogin(){
    document.querySelectorAll('.auth-modern-form').forEach(form=>{
      const emailInput=form.querySelector('input[name="username"]');
      const passwordInput=form.querySelector('input[name="password"]');
      if(!emailInput||!passwordInput||form.dataset.ajaxLogin==='1')return;
      form.dataset.ajaxLogin='1';
      form.addEventListener('submit',async e=>{
        e.preventDefault();
        let error=form.querySelector('.auth-inline-error');if(error)error.remove();
        const email=emailInput.value||'';
        const password=passwordInput.value||'';
        const next=form.querySelector('input[name="next"]')?.value||location.pathname+location.search;
        const submit=form.querySelector('.auth-primary');submit?.classList.add('is-loading');
        const token=form.querySelector('input[name="csrfmiddlewaretoken"]')?.value||csrf();
        const fd=new FormData();fd.append('email',email);fd.append('password',password);fd.append('next',next);if(token)fd.append('csrfmiddlewaretoken',token);
        try{
          const r=await fetch('/auth/login/',{method:'POST',body:fd,headers:{'X-CSRFToken':token,'X-Requested-With':'XMLHttpRequest','Accept':'application/json'}});
          let d={};try{d=await r.json()}catch(_){d={ok:false,error:'ورود انجام نشد. دوباره تلاش کن.'}}
          if(!r.ok||!d.ok){throw new Error(d.error||'ایمیل یا رمز عبور درست نیست. دوباره بررسی کن.')}
          location.href=d.redirect||'/';
        }catch(err){
          error=document.createElement('div');error.className='auth-inline-error';error.textContent=err.message||'ایمیل یا رمز عبور درست نیست.';
          const button=form.querySelector('.auth-primary');form.insertBefore(error,button);passwordInput.focus();
        }finally{submit?.classList.remove('is-loading')}
      });
    });
  }

  document.addEventListener('DOMContentLoaded',()=>{
    setupTypewriter();setupNotifications();setupEyes();setupAjaxLogin();
    setTimeout(()=>{setupEyes();setupAjaxLogin()},80);
  });
})();