(function(){
  const THEME_KEY='delta-theme';

  function applyTheme(theme){
    const value=theme==='dark'?'dark':'light';
    document.documentElement.dataset.theme=value;
    try{localStorage.setItem(THEME_KEY,value)}catch(_){ }
    document.querySelectorAll('[data-theme-choice]').forEach(btn=>btn.classList.toggle('active',btn.dataset.themeChoice===value));
  }

  try{applyTheme(localStorage.getItem(THEME_KEY)==='dark'?'dark':'light')}catch(_){applyTheme('light')}

  function installThemeSwitcher(){
    const host=document.querySelector('.mobile-drawer-panel[data-drawer-panel="main"] .mobile-drawer-links');
    if(!host||host.querySelector('.theme-switch-box'))return;
    const box=document.createElement('div');
    box.className='theme-switch-box';
    box.innerHTML=`<div class="theme-switch-title"><span>◐</span><span>ظاهر سایت</span></div><div class="theme-switch-actions"><button type="button" data-theme-choice="light"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v3M12 19v3M4.9 4.9 7 7M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1"></path></svg>روشن</button><button type="button" data-theme-choice="dark"><svg viewBox="0 0 24 24"><path d="M20 15.5A8.5 8.5 0 0 1 8.5 4 8.5 8.5 0 1 0 20 15.5Z"></path></svg>تاریک</button></div>`;
    host.appendChild(box);
    box.querySelectorAll('[data-theme-choice]').forEach(btn=>btn.addEventListener('click',()=>applyTheme(btn.dataset.themeChoice)));
    const current=document.documentElement.dataset.theme||'light';
    box.querySelectorAll('[data-theme-choice]').forEach(btn=>btn.classList.toggle('active',btn.dataset.themeChoice===current));
  }

  function installProgressiveAuth(){
    const overlay=document.getElementById('auth-overlay');
    if(!overlay)return;
    const panel=overlay.querySelector('.auth-form-panel');
    if(!panel)return;
    const csrf=(document.querySelector('#auth-overlay input[name="csrfmiddlewaretoken"]')||{}).value||'';
    const nextPath=window.location.pathname+window.location.search;

    panel.innerHTML=`
      <div class="auth-progress-shell">
        <div class="auth-progress-step active" data-auth-step="email">
          <div class="auth-heading"><h2>ورود یا ثبت‌نام</h2><p>برای ادامه فقط ایمیل خود را وارد کنید.</p></div>
          <form class="auth-modern-form" id="email-first-form" novalidate>
            <label><span>ایمیل</span><div class="auth-input"><i>✉</i><input id="auth-first-email" type="email" autocomplete="email" placeholder="example@email.com" required></div></label>
            <div class="auth-progress-error" id="auth-email-error"></div>
            <div class="auth-progress-loading" id="auth-email-loading">در حال بررسی ایمیل...</div>
            <button class="auth-primary" type="submit">ادامه</button>
          </form>
        </div>
        <div class="auth-progress-step" data-auth-step="login">
          <button class="auth-progress-back" type="button" data-auth-back>› تغییر ایمیل</button>
          <div class="auth-heading"><h2>خوش آمدید</h2><p>این ایمیل قبلاً ثبت شده؛ رمز عبور را وارد کنید.</p></div>
          <div class="auth-email-preview"><span data-auth-email-preview></span><button type="button" data-auth-back>ویرایش</button></div>
          <form method="post" action="/login/" class="auth-modern-form">
            <input type="hidden" name="csrfmiddlewaretoken" value="${csrf}"><input type="hidden" name="next" value="${nextPath}"><input type="hidden" name="username" data-auth-email-hidden>
            <label><span>رمز عبور</span><div class="auth-input"><i>⌑</i><input id="auth-login-password" type="password" name="password" required autocomplete="current-password" placeholder="رمز عبور"></div></label>
            <div class="auth-form-row"><span></span><a href="/password-reset/">فراموشی رمز عبور</a></div>
            <button class="auth-primary" type="submit">ورود به حساب</button>
          </form>
        </div>
        <div class="auth-progress-step" data-auth-step="register">
          <button class="auth-progress-back" type="button" data-auth-back>› تغییر ایمیل</button>
          <div class="auth-heading"><h2>ساخت حساب جدید</h2><p>این ایمیل هنوز عضو نیست. مشخصات زیر را کامل کنید.</p></div>
          <div class="auth-email-preview"><span data-auth-email-preview></span><button type="button" data-auth-back>ویرایش</button></div>
          <form method="post" action="/register/" class="auth-modern-form">
            <input type="hidden" name="csrfmiddlewaretoken" value="${csrf}"><input type="hidden" name="email" data-auth-email-hidden>
            <div class="auth-name-row"><label><span>نام</span><div class="auth-input"><input type="text" name="first_name" required autocomplete="given-name" placeholder="نام"></div></label><label><span>نام خانوادگی</span><div class="auth-input"><input type="text" name="last_name" required autocomplete="family-name" placeholder="نام خانوادگی"></div></label></div>
            <label><span>رمز عبور</span><div class="auth-input"><input type="password" name="password1" required autocomplete="new-password" placeholder="رمز عبور"></div></label>
            <label><span>تکرار رمز عبور</span><div class="auth-input"><input type="password" name="password2" required autocomplete="new-password" placeholder="تکرار رمز عبور"></div></label>
            <label class="auth-check auth-terms"><input type="checkbox" required><span>قوانین و شرایط استفاده از فروشگاه را می‌پذیرم.</span></label>
            <button class="auth-primary" type="submit">ثبت‌نام و ساخت حساب</button>
          </form>
        </div>
      </div>`;

    const emailInput=panel.querySelector('#auth-first-email');
    const errorBox=panel.querySelector('#auth-email-error');
    const loading=panel.querySelector('#auth-email-loading');
    let currentEmail='';

    function showStep(name){
      panel.querySelectorAll('[data-auth-step]').forEach(step=>step.classList.toggle('active',step.dataset.authStep===name));
      if(name==='email'){setTimeout(()=>emailInput&&emailInput.focus(),80)}
      if(name==='login'){setTimeout(()=>panel.querySelector('#auth-login-password')?.focus(),80)}
    }
    function syncEmail(email){
      currentEmail=email;
      panel.querySelectorAll('[data-auth-email-preview]').forEach(el=>el.textContent=email);
      panel.querySelectorAll('[data-auth-email-hidden]').forEach(el=>el.value=email);
    }
    function reset(){
      currentEmail='';
      emailInput.value='';
      errorBox.classList.remove('show');
      errorBox.textContent='';
      loading.classList.remove('show');
      panel.querySelectorAll('input[type="password"],input[name="first_name"],input[name="last_name"]').forEach(el=>el.value='');
      showStep('email');
    }

    panel.querySelector('#email-first-form').addEventListener('submit',async function(e){
      e.preventDefault();
      const email=(emailInput.value||'').trim().toLowerCase();
      errorBox.classList.remove('show');
      if(!email||!emailInput.checkValidity()){
        errorBox.textContent='یک ایمیل معتبر وارد کنید.';errorBox.classList.add('show');emailInput.focus();return;
      }
      loading.classList.add('show');
      try{
        const res=await fetch('/auth/email-check/?email='+encodeURIComponent(email),{headers:{'X-Requested-With':'XMLHttpRequest'}});
        const data=await res.json();
        if(!res.ok||!data.ok)throw new Error(data.error||'بررسی ایمیل انجام نشد.');
        syncEmail(email);
        showStep(data.exists?'login':'register');
      }catch(err){
        errorBox.textContent=err.message||'خطا در بررسی ایمیل. دوباره تلاش کنید.';errorBox.classList.add('show');
      }finally{loading.classList.remove('show')}
    });

    panel.querySelectorAll('[data-auth-back]').forEach(btn=>btn.addEventListener('click',()=>{emailInput.value=currentEmail;showStep('email')}));
    document.querySelectorAll('.store-auth-open').forEach(btn=>btn.addEventListener('click',()=>setTimeout(reset,0)));
    reset();
  }

  document.addEventListener('DOMContentLoaded',function(){installThemeSwitcher();installProgressiveAuth()});
})();
