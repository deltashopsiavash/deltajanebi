document.addEventListener('DOMContentLoaded',function(){
  document.querySelectorAll('.auth-pane[data-auth-pane="register"] .auth-terms span').forEach(function(span){
    if(span.querySelector('a[href="/terms/"]')) return;
    span.innerHTML='<a href="/terms/" target="_blank" rel="noopener" style="font-weight:900;color:var(--sf-blue);text-decoration:none">قوانین و شرایط استفاده از فروشگاه</a> را می‌پذیرم.';
  });
});
