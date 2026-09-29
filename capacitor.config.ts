import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.naqaaholding.market',
  appName: 'NAQAA Market',
  webDir: 'static',
  bundledWebRuntime: false,
  server: {
    androidScheme: 'https'
  }
};

export default config;
