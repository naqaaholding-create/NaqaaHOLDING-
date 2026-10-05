/* NAQAA Market — External Wallet bridge.
 * Loaded only after the user explicitly chooses the external wallet.
 * Never requests or stores a seed phrase/private key.
 */
(async () => {
  const cfg = window.NAQAA_REOWN_CONFIG;
  if (!cfg?.projectId) return;
  let modal = null, initializing = null;

  async function init() {
    if (modal) return modal;
    if (initializing) return initializing;
    initializing = (async () => {
      const [{ createAppKit }, { EthersAdapter }, { bsc }] = await Promise.all([
        import("https://esm.sh/@reown/appkit@1.8.24"),
        import("https://esm.sh/@reown/appkit-adapter-ethers@1.8.24"),
        import("https://esm.sh/@reown/appkit@1.8.24/networks")
      ]);
      modal = createAppKit({
        adapters: [new EthersAdapter()],
        networks: [bsc],
        defaultNetwork: bsc,
        projectId: cfg.projectId,
        metadata: {
          name: "NAQAA MARKET",
          description: "سوق نقاء — المحفظة الخارجية",
          url: "https://naqaaholding-create.github.io/NaqaaHOLDING-",
          icons: [],
          redirect: {
            native: "com.naqaaholding.market://wallet",
            universal: "https://naqaaholding-create.github.io/NaqaaHOLDING-/wallet",
            linkMode: true
          }
        },
        features: { analytics:false, swaps:false, onramp:false, email:false, socials:false },
        themeMode: "light"
      });
      window.NAQAA_REOWN_APPKIT = modal;
      if (typeof modal.subscribeAccount === "function") {
        modal.subscribeAccount((account) => {
          const address = account?.address || account?.caipAddress?.split(":").pop();
          if (!account?.isConnected || !address) return;
          window.dispatchEvent(new CustomEvent("naqaa:wallet-connected", {
            detail: { provider:"External Wallet", address, network:"BEP20", chainId:56 }
          }));
        });
      }
      return modal;
    })().catch(e => { initializing=null; throw e; });
    return initializing;
  }

  // Deliberately does NOT open anything on page load.
  window.NAQAA_REOWN_OPEN = async () => {
    const m = await init();
    if (m?.open) return m.open();
  };
})();