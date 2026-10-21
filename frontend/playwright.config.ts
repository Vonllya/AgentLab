import {defineConfig} from '@playwright/test';
const testData=process.env.AGENTLAB_E2E_DATA || `/tmp/agentlab-playwright-${process.pid}-${Date.now()}`;
process.env.AGENTLAB_E2E_DATA=testData;
export default defineConfig({testDir:'./e2e',use:{baseURL:'http://127.0.0.1:5173',headless:true,launchOptions:process.env.AGENTLAB_BROWSER_EXECUTABLE?{executablePath:process.env.AGENTLAB_BROWSER_EXECUTABLE}:undefined},webServer:[{command:'../.venv/bin/uvicorn backend.app:app --app-dir .. --host 127.0.0.1 --port 8000',port:8000,reuseExistingServer:!!process.env.AGENTLAB_REUSE_SERVER,env:{AGENTLAB_DATA:testData,AGENT_MODE:'mock'}},{command:'npm run dev -- --host 127.0.0.1',port:5173,reuseExistingServer:!!process.env.AGENTLAB_REUSE_SERVER}]});
