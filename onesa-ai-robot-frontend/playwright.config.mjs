import {defineConfig,devices} from '@playwright/test';
import {qualificationHeaders} from './tests/browser/qualification-headers.mjs';
const requireReferenceRendering = process.env.ROBOT_REQUIRE_WEBGL === 'true';
export default defineConfig({
  testDir:'tests/browser',workers:1,retries:0,
  reporter:[['list'],['json',{outputFile:'test-results/results.json'}]],
  use:{timezoneId:'Europe/Berlin',extraHTTPHeaders:qualificationHeaders(process.env.ROBOT_BROWSER_URL || 'http://localhost:3431',process.env.VERCEL_AUTOMATION_BYPASS_SECRET),baseURL:process.env.ROBOT_BROWSER_URL || 'http://localhost:3431'},
  projects:[
    {name:'chromium',use:{...devices['Desktop Chrome']}},
    // Stock Linux headless Firefox disables WebGL 2. CI supplies an isolated Xvfb display.
    {name:'firefox',use:{...devices['Desktop Firefox'],...(requireReferenceRendering ? {headless:false} : {})}},
    {name:'webkit',use:{...devices['Desktop Safari']}},
  ],
});
