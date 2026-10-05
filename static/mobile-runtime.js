/* NAQAA mobile runtime: safe Android/iPhone navigation */
(function(){
  function goBack(){
    if(window.history.length>1){ window.history.back(); return; }
    var role=localStorage.getItem('naqaa_role')||'';
    if(role==='company_director'||role==='manager'||role==='hr_manager'||role==='finance_manager'||role==='sales_manager'||role==='listing_manager'||role==='compliance_manager'||role==='customer_service'||role==='employee'){
      location.href='account.html';
    }else{
      location.href='account.html?login=1';
    }
  }
  try{
    var App=window.Capacitor&&window.Capacitor.Plugins&&window.Capacitor.Plugins.App;
    if(App&&App.addListener){
      App.addListener('backButton',function(){goBack();});
    }
  }catch(e){}
  window.NAQAA_GO_BACK=goBack;
})();
