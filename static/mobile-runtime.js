/* NAQAA mobile runtime: safe Android/iPhone navigation */
(function(){
  function isAuth(){
    return !!(localStorage.getItem('naqaa_token')||localStorage.getItem('token'));
  }
  function goBack(){
    var path=(location.pathname||'').toLowerCase();
    var role=localStorage.getItem('naqaa_role')||'';
    if(!isAuth()){
      if(!path.endsWith('/account.html') && !path.endsWith('account.html')) location.replace('account.html?login=1');
      return;
    }
    if(path.endsWith('/account.html') || path.endsWith('account.html')){
      return;
    }
    if(window.history.length>1){
      window.history.back();
      return;
    }
    var staff=['company_director','manager','hr_manager','finance_manager','sales_manager','listing_manager','compliance_manager','customer_service','employee'];
    location.replace(staff.includes(role)?'account.html':'market.html');
  }
  try{
    var App=window.Capacitor&&window.Capacitor.Plugins&&window.Capacitor.Plugins.App;
    if(App&&App.addListener){ App.addListener('backButton',function(){goBack();}); }
  }catch(e){}
  window.NAQAA_GO_BACK=goBack;
})();