@echo off
REM Double-click to start Bid Desk watching a folder.
REM
REM Edit the three paths below to match the client's setup, then leave this
REM running. Tenders dropped into INBOX get a report in REPORTS.

setlocal

set CORPUS=%~dp0samples\corpus
set INBOX=%~dp0inbox
set REPORTS=%~dp0reports

echo.
echo   Bid Desk - watching for tenders
echo   ------------------------------------------------------------
echo   Drop a tender (.pdf .docx .xlsx .md .txt) into:
echo     %INBOX%
echo.
echo   Reports appear in:
echo     %REPORTS%
echo.
echo   Close this window to stop.
echo   ------------------------------------------------------------
echo.

python -m biddesk watch --corpus "%CORPUS%" --inbox "%INBOX%" --out "%REPORTS%"

if errorlevel 1 (
  echo.
  echo   Bid Desk stopped with an error. The message above says why.
  echo.
  pause
)

endlocal
