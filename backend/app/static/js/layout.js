(function(){
  const root = document.getElementById('layout');
  const btnCollapse = document.getElementById('btnCollapse');
  const btnExpand  = document.getElementById('btnExpand');
  const topbar = document.getElementById('topbar');

  /* ========= 狀態：收合/展開 ========= */
  const KEY = 'sidebar-state'; // 'collapsed' or 'expanded'

  function setCollapsed(){
    root.classList.add('sidebar-collapsed');
    localStorage.setItem(KEY, 'collapsed');
  }
  function setExpanded(){
    root.classList.remove('sidebar-collapsed');
    localStorage.setItem(KEY, 'expanded');
  }

  // 載入上次狀態
  (function loadState(){
    const st = localStorage.getItem(KEY);
    if (st === 'collapsed') setCollapsed();
    else setExpanded(); // 預設展開
  })();

  // 點 X/≡ 才改變狀態
  btnCollapse?.addEventListener('click', setCollapsed);
  btnExpand?.addEventListener('click', setExpanded);

  /* ========= Sticky topbar 陰影 ========= */
  function onScroll(){
    if (window.scrollY > 0) topbar.classList.add('is-scrolled');
    else topbar.classList.remove('is-scrolled');
  }
  window.addEventListener('scroll', onScroll, {passive:true});
  onScroll();

  /* ========= 收合時 Tooltip ========= */
  const enableTooltips = () => {
    const isCollapsed = root.classList.contains('sidebar-collapsed');
    document.querySelectorAll('.s-link').forEach(el=>{
      const title = el.getAttribute('title');
      if (!title) return;
      if (isCollapsed){
        if (!el._tip) el._tip = new bootstrap.Tooltip(el);
      }else{
        el._tip?.dispose?.(); el._tip = null;
      }
    });
  };
  enableTooltips();
  btnCollapse?.addEventListener('click', () => setTimeout(enableTooltips, 200));
  btnExpand?.addEventListener('click', () => setTimeout(enableTooltips, 200));
})();
