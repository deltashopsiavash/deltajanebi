(function(){
  function ensureProgressivePhone(){
    const form=document.querySelector('[data-auth-step="register"] form');
    if(!form||form.querySelector('input[name="phone"]'))return;
    const firstPassword=form.querySelector('input[name="password1"]')?.closest('label');
    if(!firstPassword)return;
    const label=document.createElement('label');
    label.innerHTML='<span>شماره موبایل</span><div class="auth-input"><input type="tel" name="phone" required autocomplete="tel" inputmode="tel" placeholder="09xxxxxxxxx" dir="ltr"></div>';
    form.insertBefore(label,firstPassword);
    const submit=form.querySelector('.auth-primary');
    if(submit)submit.textContent='ثبت‌نام و دریافت کد تأیید';
  }

  function setupStories(){
    const dialog=document.getElementById('product-story-dialog');
    if(!dialog||dialog.dataset.ready==='1')return;
    dialog.dataset.ready='1';
    const view=document.getElementById('product-story-view');
    const title=document.getElementById('product-story-title');
    const target=document.getElementById('product-story-target');
    const close=()=>{try{dialog.close()}catch(_){dialog.removeAttribute('open')}if(view)view.innerHTML='';document.body.classList.remove('modal-open')};
    document.querySelectorAll('[data-story-open]').forEach(button=>button.addEventListener('click',()=>{
      if(!view)return;
      view.innerHTML='';
      const src=button.dataset.media||'';
      if(button.dataset.type==='video'){
        const video=document.createElement('video');video.src=src;video.controls=true;video.autoplay=true;video.playsInline=true;video.preload='metadata';view.appendChild(video);
      }else{
        const image=document.createElement('img');image.src=src;image.alt=button.dataset.title||'معرفی محصول';view.appendChild(image);
      }
      if(title)title.textContent=button.dataset.title||'';
      if(target){target.href=button.dataset.target||'#';target.hidden=!button.dataset.target;}
      try{dialog.showModal()}catch(_){dialog.setAttribute('open','open')}
      document.body.classList.add('modal-open');
    }));
    dialog.querySelectorAll('[data-story-close]').forEach(x=>x.addEventListener('click',close));
    dialog.addEventListener('click',e=>{if(e.target===dialog)close()});
    dialog.addEventListener('cancel',e=>{e.preventDefault();close()});
  }

  function polishPasswordButtons(){
    document.querySelectorAll('.password-wrap').forEach(wrap=>{
      const buttons=wrap.querySelectorAll('.password-eye');
      buttons.forEach((button,index)=>{if(index>0)button.remove()});
    });
  }

  function init(){
    ensureProgressivePhone();
    setTimeout(ensureProgressivePhone,100);
    setupStories();
    polishPasswordButtons();
    setTimeout(polishPasswordButtons,120);
  }

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});
  else init();
})();
