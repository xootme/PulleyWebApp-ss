# NIST STEP File Analyzer (SFA) 5.45

Binaries are not committed to git (too large). Download and extract here:

1. Download `SFA-5.45.zip` from:
   https://github.com/usnistgov/SFA/raw/master/Release/SFA-5.45.zip

2. Extract into this folder (`tools/sfa/`). You need:
   - `sfa-cl.exe`  — command-line validator (used by tests)
   - `STEP-File-Analyzer.exe`  — GUI version (optional)

3. First run installs the IFCsvr toolkit automatically:
   ```
   tools\sfa\sfa-cl.exe sample.step syntax noopen nolog
   ```
   Accept the IFCsvr installer prompt (one-time, needs admin or will auto-install to user AppData).

4. Validate STEP files:
   ```
   python tests\validate_nist_sfa.py path\to\file.step
   python tests\validate_nist_sfa.py tests\step_samples\
   ```
