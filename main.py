"""Run the workflow: python3 main.py <script.txt> [video_analysis.txt] [--provider main|single|hybrid]
(The code lives in the medical_brain/ package; see README "Project structure".)"""
from medical_brain.cli import main

if __name__ == "__main__":
    main()
