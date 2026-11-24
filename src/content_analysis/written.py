import re, statistics
from .store import digest

def analyze(text):
    paragraphs=[x.strip() for x in re.split(r'\n\s*\n|\n',text) if x.strip()]
    passages=[];cursor=0
    for i,p in enumerate(paragraphs):
        start=text.find(p,cursor);end=start+len(p);cursor=end
        passages.append({'id':'p'+str(i+1)+'-'+digest(p)[:8],'kind':'passage','start_char':start,'end_char':end,'text':p,'position':i+1})
    sentences=[s.strip() for s in re.split(r'(?<=[.!?])\s+',text) if s.strip()];lengths=[len(re.findall(r"\b[\w'-]+\b",s)) for s in sentences];words=re.findall(r"\b[\w'-]+\b",text)
    return passages,{'word_count':len(words),'paragraph_count':len(passages),'sentence_count':len(sentences),'sentence_words_mean':round(statistics.mean(lengths),1) if lengths else 0,'sentence_words_range':[min(lengths),max(lengths)] if lengths else [0,0],'questions':text.count('?'),'numeric_tokens':re.findall(r'\b\d[\d.,%]*',text),'note':'Measured text structure only. Argument, tone, effectiveness and intent require ChatGPT analysis against anchored passages. One reference does not establish an author pattern.'}
