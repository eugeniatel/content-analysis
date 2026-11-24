"""Run the maintained analyzer's measurements without its optional chat formatter.

The shared formatter raises KeyError on a single-shot reference. Content Analysis
consumes report.json, so it does not need that Markdown report. Keep the shared
skill and its measurement code untouched.
"""
from .media import reference

def main():
    analyzer=reference()
    analyzer.chat_report=lambda *args,**kwargs: ''
    analyzer.main()

if __name__=='__main__':main()
