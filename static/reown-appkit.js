/* NAQAA Market — Reown AppKit bridge.
 * Wallet connection only. No transaction signing or transfers are enabled here.
 * AppKit is loaded as an ES module for the static/Capacitor web layer.
 */
(async () => {
  const cfg = window.NAQAA_REOWN_CONFIG;
  if (!cfg?.projectId) return;

  try {
    const [{ createAppKit }, { EthersAdapter }, { bsc }] = await Promise.all([
      import("https://esm.sh/@reown/appkit@1.8.24"),
      import("https://esm.sh/@reown/appkit-adapter-ethers@1.8.24"),
      import("https://esm.sh/@reown/appkit@1.8.24/networks")
    ]);

    const metadata = {
      name: "NAQAA MARKET",
      description: "سوق نقاء — ربط محفظة خارجية",
      url: "https://naqaaholding-create.github.io/NaqaaHOLDING-",
      icons: [],
      redirect: {
        native: "com.naqaaholding.market://wallet",
        universal: "https://naqaaholding-create.github.io/NaqaaHOLDING-/wallet",
        linkMode: true
      }
    };

    const modal = createAppKit({
      adapters: [new EthersAdapter()],
      networks: [bsc],
      defaultNetwork: bsc,
      projectId: cfg.projectId,
      metadata,
      features: {
        analytics: false,
        swaps: false,
        onramp: false,
        email: false,
        socials: false
      },
      themeMode: "light"
    });

    window.NAQAA_REOWN_APPKIT = modal;
    window.NAQAA_REOWN_OPEN = () => modal.open();

    // AppKit exposes the connected EVM account through subscribeAccount().
    // We only pass the public address to the page; no signing or transfer is requested.
    if (typeof modal.subscribeAccount === "function") {
      modal.subscribeAccount((account) => {
        const address = account?.address || account?.caipAddress?.split(":").pop();
        if (!account?.isConnected || !address) return;
        window.dispatchEvent(new CustomEvent("naqaa:wallet-connected", {
          detail: {
            provider: "Reown",
            address,
            network: "BEP20",
            chainId: 56
          }
        }));
      });
    }

    const state = document.getElementById("linkResult");
    if (state) {
      state.style.display = "block";
      state.textContent = "✓ تم تجهيز اتصال Reown. اضغط «اتصال المحفظة الخارجية / Reown» لفتح قائمة المحافظ.";
    }
  } catch (error) {
    console.warn("NAQAA Reown initialization failed:", error);
  }
})();
