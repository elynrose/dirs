; Shown after files are installed (non-silent installs). One click — explains first-run Docker picker.
!macro customInstall
  MessageBox MB_OK|MB_ICONINFORMATION "Directely: If Docker is not found on first start, you will be asked to locate docker.exe.$\n$\nTypical Docker Desktop path:$\nC:\Program Files\Docker\Docker\resources\bin\docker.exe$\n$\nYou can also set DOCKER_BIN in the app .env (see README)." /SD IDOK
!macroend
