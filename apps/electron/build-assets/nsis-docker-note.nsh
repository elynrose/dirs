; Shown after files are installed (non-silent installs).
!macro customInstall
  MessageBox MB_OK|MB_ICONINFORMATION "Directely requires Docker Desktop.$\n$\nIf Docker is not installed, the app will open the download page on first start.$\n$\nInstall from:$\nhttps://www.docker.com/products/docker-desktop/$\n$\nThen start Docker Desktop once before launching Directely." /SD IDOK
!macroend
