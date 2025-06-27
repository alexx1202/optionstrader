@echo off
REM This batch file pushes everything inside the PUSH folder to the repo

REM Change directory to the location of this script
pushd "%~dp0"

REM Stage all changes in this folder
git add .

REM Commit the changes with a standard message
git commit -m "Add files from PUSH folder"

REM Push the commit to the remote repository
git push

popd
