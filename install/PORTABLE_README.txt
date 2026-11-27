GFT_Analysis_Tool - Portable Windows Application

STARTING THE APPLICATION
1. Extract the entire ZIP to a folder your organization allows you to run from.
2. Open the extracted "GFT_Analysis_Tool_Portable" folder.
3. Double-click "GFT_Analysis_Tool.exe".

Keep the executable and its _internal folder together. Do not run inside the ZIP.
Python, uv, PowerShell, administrator access, and Internet downloads are not
required by the application at runtime. Use a writable location for saved work.
This is a Windows x64 package; it is not a macOS, Linux, or Windows ARM build.

OPENING A DRAWING
Use File > Open and select a JSON file in "One-Line Layouts".
The saved sample current values are retained and are not manufacturer ratings.
Review protection-zone boundaries and operating loads in Configure Zones before
confirming them. Incomplete zones remain drafts; provisional PDFs can still be
exported. Use Run Evaluation, then File > Export Results for a calculation PDF.
Original reference PDFs are not included. You can import an approved local PDF.
Save edited layouts and exported PDFs in your own writable project folder.

FOR IT REVIEW
This package is unsigned. Organization policies may block it until IT approves
or signs it. Do not bypass security controls; ask IT to approve deployment.
No online installer, package manager, or PowerShell launch script is used.
BUILD INFO.json records the build and bundled library versions. The adjacent
ZIP.sha256 file provides a checksum for the original distribution archive.
Third-Party Notices contains the installed libraries' metadata and supplied
license notices. Application Source.zip contains the current application source,
required one-line layouts, license, and build inputs. It can be rebuilt after
extraction without Git; saved source provenance is used for build information.

BUILDING AN UPDATED PACKAGE (DEVELOPER MACHINE ONLY)
From the repository or extracted Application Source.zip, install dependencies:
  uv sync --locked --group package
Then build and package:
  .venv\Scripts\python.exe install\build_portable.py
The resulting ZIP is dist\GFT_Analysis_Tool-Windows-Portable.zip.
