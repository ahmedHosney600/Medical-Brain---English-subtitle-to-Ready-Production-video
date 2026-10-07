"""Convert a markdown script to HTML/PDF/DOCX: python3 convert_script.py <file.md>
Kept at the old path; the code now lives in medical_brain/exports/convert.py."""
from medical_brain.exports.convert import *  # noqa: F401,F403
from medical_brain.exports.convert import main

if __name__ == "__main__":
    main()
