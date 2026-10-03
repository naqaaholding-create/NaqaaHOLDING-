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
      url: window.location.origin,
      icons: []
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

    const state = document.getElementById("linkResult");
    if (state) {
      state.style.display = "block";
      state.textContent = "✓ تم تجهيز اتصال Reown. اضغط «اتصال SafePal / Reown» لفتح قائمة المحافظ.";
    }
  } catch (error) {
    console.warn("NAQAA Reown initialization failed:", error);
  }
})();
