const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  saveReportPdf: (batchId) => ipcRenderer.invoke('save-report-pdf', { batchId }),
});
