const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("directorDesktop", {
  isDesktop: true,
  getDockerConfig: () => ipcRenderer.invoke("desktop:getDockerConfig"),
  browseDockerExe: () => ipcRenderer.invoke("desktop:browseDockerExe"),
  testDocker: (exe) => ipcRenderer.invoke("desktop:testDocker", exe),
  setDockerExe: (exe) => ipcRenderer.invoke("desktop:setDockerExe", exe),
  clearDockerExe: () => ipcRenderer.invoke("desktop:clearDockerExe"),
  openUserDataFolder: () => ipcRenderer.invoke("desktop:openUserDataFolder"),
});
