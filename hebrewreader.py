#!/usr/bin/env python3
from argparse import ArgumentParser, FileType, RawTextHelpFormatter
import os
import pickle
import re
from shutil import copyfile
import subprocess
import sys
import tempfile
import textwrap

from tf.fabric import Fabric

import minitf

DATADIR = 'data'

PASSAGE_RGX = (
    r'^(?P<book>(?:\d )?[a-zA-Z ]+) '
    r'(?P<startchap>\d+)(?::(?P<startverse>\d+))?'
    r'(?:-(?P<endref>(?P<endchap>\d+)(?::(?P<endverse>\d+))?|(?:book)?end))?$'
)

FEATURES = 'g_word_utf8 gloss lex_utf8 otype trailer_utf8 voc_lex_utf8'
SYR_FEATURES = 'otype trailer word'
GRK_FEATURES = 'g_cons_utf8 gloss lex_utf8 otype word'

VERSE_NODES = dict()

def load_verse_nodes():
    global VERSE_NODES
    with open(os.path.join(DATADIR, 'verse_nodes.pkl'), 'rb') as f:
        VERSE_NODES = pickle.load(f)

def parse_passage(passage, lang):
    match = re.match(PASSAGE_RGX, passage)
    print(match)
    if match is None:
        match = {'book': passage, 'startchap': 1, 'startverse': 1,
                'endchap': None, 'endverse': None, 'endref': 'bookend'}
    else:
        match = match.groupdict()
    print(match)
    match['book'] = match['book'].replace(' ', '_')
    match['startchap'] = int(match['startchap'])
    if match['startverse'] is not None:
        match['startverse'] = int(match['startverse'])
    if match['endchap'] is not None:
        match['endchap'] = int(match['endchap'])
    if match['endverse'] is not None:
        match['endverse'] = int(match['endverse'])
    try:
        if match['endchap'] is None:
            if match['startverse'] is None and match['endref'] is None:
                match['endref'] = 'end'
            else:
                match['endchap'] = match['startchap']
                match['endverse'] = match['startverse']
        elif match['endverse'] is None:
            match['endverse'] = len(VERSE_NODES[lang][match['book']][match['endchap']])
        if match['startverse'] is None:
            match['startverse'] = 1
        if match['endref'] == 'end':
            match['endchap'] = match['startchap']
            match['endverse'] = len(VERSE_NODES[lang][match['book']][match['endchap']])
        elif match['endref'] == 'bookend':
            match['endchap'] = len(VERSE_NODES[lang][match['book']])
            match['endverse'] = len(VERSE_NODES[lang][match['book']][match['endchap']])
    except:
        raise ValueError('Could not find reference "{}" in the {} language'.format(passage, lang))
    match.pop('endref')
    return match

def verses_in_passage(passage, lang):
    for chap in range(passage['startchap'], passage['endchap']+1):
        start = passage['startverse'] if chap == passage['startchap'] else 1
        if chap == passage['endchap']:
            end = passage['endverse']
        else:
            end = len(VERSE_NODES[lang][passage['book']][chap])

        for verse in range(start, end+1):
            yield (passage['book'], chap, verse)

def fix_trailer(trailer):
    return trailer\
            .replace('\n', '')\
            .replace('\u05e1', r'\setuma{}')\
            .replace('\u05e4', r'\petucha{}')

def fix_gloss(gloss):
    if gloss == 'i':
        return 'I'
    return re.sub(r'<(.*)>', r'\\textit{\1}', gloss)

def get_passage_and_words(passage, api, lang, separate_chapters=True, verse_nos=True):
    text = []
    words = set()

    last_chapter = None
    for verse in verses_in_passage(passage, lang):
        if verse[1] != last_chapter:
            last_chapter = verse[1]
            if separate_chapters:
                text.append('\n')
        node = VERSE_NODES[lang][verse[0]][verse[1]][verse[2]]
        wordnodes = api.L.d(node, otype='word')
        thistext = ''
        if verse_nos:
            if verse[2] == 1:
                thistext += r'\rdrchap{%d}' % verse[1]
            thistext += r'\rdrverse{%d} ' % verse[2]
        thiswords = []
        for word in wordnodes:
            if lang == 'hebrew':
                thiswords.append(
                        api.F.g_word_utf8.v(word) +
                        fix_trailer(api.F.trailer_utf8.v(word)))
                lex = api.L.u(word, otype='lex')[0]
                words.add((api.F.lex_utf8.v(word), api.F.voc_lex_utf8.v(lex), fix_gloss(api.F.gloss.v(lex))))
            elif lang == 'syriac':
                thiswords.append(
                        api.F.word.v(word) +
                        fix_trailer(api.F.trailer.v(word)))
            elif lang == 'greek':
                thiswords.append(
                        api.F.word.v(word) + " ")
                # lex = api.L.u(word, otype='lex')[0]
                words.add((api.F.lex_utf8.v(word), api.F.lex_utf8.v(word), fix_gloss(api.F.gloss.v(word))))
        thistext += ''.join(thiswords)
        text.append(thistext)

    return text, sorted(words)

def load_data(passage, lang):
    seen = set()
    context = dict()
    for book, chap, _ in verses_in_passage(passage, lang):
        if (book,chap) in seen:
            continue
        seen.add((book, chap))
        fname = lang + '_' + book + '_' + str(chap) + '.pkl'
        with open(os.path.join(DATADIR, fname), 'rb') as f:
            add_context = pickle.load(f)
            for key, val in add_context.items():
                if key not in context:
                    context[key] = val
                elif key == 'nodes':
                    context[key] += ',' + val
                elif key == 'locality' or key == 'features':
                    for subkey, subval in val.items():
                        context[key][subkey].update(subval)
                else:
                    context[key].update(val)
    api = minitf.MiniApi(**context)
    return api

def generate(passages, include_voca, combine_voca, clearpage_before_voca,
        large_text, larger_text, tex, pdf, templates, lang, quiet=False):
    lang = lang[0]
    if lang != "greek":
        tex.write(templates['pre'])
    else:
        tex.write(templates['greek_pre'])

    if large_text:
        tex.write('\\largetexttrue\n')
    if larger_text:
        tex.write('\\largertexttrue\n')

    voca = set()

    for passage_text in passages:
        passage = parse_passage(passage_text, lang)

        passage_pretty = '{} {}:{} - {}:{}'.format(
            passage['book'].replace('_', ' '),
            passage['startchap'], passage['startverse'],
            passage['endchap'], passage['endverse'])
        tex.write(r'\def\thepassage{%s}' % passage_pretty)

        api = load_data(passage, lang)
        text, words = get_passage_and_words(passage, api, lang)

        if lang == "hebrew":
            tex.write('\n\n' + templates['pretext'])
        elif lang == "syriac":
            tex.write('\n\n' + templates['pretext_syr'])
        elif lang == "greek":
            tex.write('\n\n' + templates['pretext_grk'])
        tex.write('\n'.join(text))
        if lang == "hebrew":
            tex.write('\n' + templates['posttext'])
        elif lang == "syriac":
            tex.write('\n' + templates['posttext_syr'])
        elif lang == "greek":
            tex.write('\n' + templates['posttext_grk'])

        if not include_voca:
            continue

        if combine_voca:
            voca.update(words)
        else:
            if clearpage_before_voca:
                tex.write('\n\n\\clearpage')
            tex.write('\n\n' + templates['prevoca'])
            if lang == "hebrew":
                tex.write('\\\\\n'.join(r'{\hebrewfont\vocafontsize\RL{%s}} \begin{english}%s\end{english}' % (lex,gloss) for _, lex, gloss in words))
            elif lang == "syriac":
                tex.write('\\\\\n'.join(r'{\syriacfont\vocafontsize\RL{%s}} \begin{english}%s\end{english}' % (lex,gloss) for _, lex, gloss in words))
            elif lang == "greek":
                tex.write('\\\\\n'.join(r'{\greekfont\vocafontsize\RL{%s}} \begin{english}%s\end{english}' % (lex,gloss) for _, lex, gloss in words))
            tex.write('\n' + templates['postvoca'])

    if include_voca and combine_voca and lang != 'syriac':
        if clearpage_before_voca:
            tex.write('\n\n\\clearpage')
        tex.write('\n\n' + templates['prevoca'])
        if lang == "hebrew":
            tex.write('\\\\\n'.join(r'{\hebrewfont\RL{%s}} \begin{english}%s\end{english}' % (lex,gloss) for _, lex, gloss in sorted(voca)))
        elif lang == "syriac":
            tex.write('\\\\\n'.join(r'{\syriacfont\RL{%s}} \begin{english}%s\end{english}' % (lex,gloss) for _, lex, gloss in sorted(voca)))
        elif lang == "greek":
            tex.write('\\\\\n'.join(r'{\greekfont\RL{%s}} \begin{english}%s\end{english}' % (lex,gloss) for _, lex, gloss in sorted(voca)))
        tex.write('\n' + templates['postvoca'])

    tex.write(templates['post'])

    tex.close()

    if pdf is None:
        return (tex.name, None)

    path, filename = os.path.split(pdf)
    jobname, _ = os.path.splitext(filename)

    cmd = ['xelatex']
    if path != '':
        cmd.append('-output-directory')
        cmd.append(path)
    cmd.append('-jobname')
    cmd.append(jobname)
    cmd.append(tex.name)

    if quiet:
        null = open(os.devnull, 'wb')
        subprocess.call(cmd, stdout=null, stderr=null)
    else:
        subprocess.run(cmd)

    return (tex.name, pdf)

def main():
    parser = ArgumentParser(
            description='LaTeX reader generator for Biblical Hebrew',
            formatter_class=RawTextHelpFormatter)

    p_tex = parser.add_argument_group('TeX template options')
    p_tex.add_argument('--pre-tex', type=FileType('r', encoding='utf-8'),
            metavar='FILE', default=open('pre.tex', encoding='utf-8'),
            help='TeX file to prepend to output')
    p_tex.add_argument('--post-tex', type=FileType('r', encoding='utf-8'),
            metavar='FILE', default=open('post.tex', encoding='utf-8'),
            help='TeX file to append to output')
    p_tex.add_argument('--pre-text-tex', type=FileType('r', encoding='utf-8'),
            metavar='FILE', default=open('pretext.tex', encoding='utf-8'),
            help='TeX file to prepend to texts')
    p_tex.add_argument('--post-text-tex', type=FileType('r', encoding='utf-8'),
            metavar='FILE', default=open('posttext.tex', encoding='utf-8'),
            help='TeX file to append to texts')
    p_tex.add_argument('--pre-voca-tex', type=FileType('r', encoding='utf-8'),
            metavar='FILE', default=open('prevoca.tex', encoding='utf-8'),
            help='TeX file to prepend to word list')
    p_tex.add_argument('--post-voca-tex', type=FileType('r', encoding='utf-8'),
            metavar='FILE', default=open('postvoca.tex', encoding='utf-8'),
            help='TeX file to append to word list')

    p_output = parser.add_argument_group('Output options')
    p_output.add_argument('--tex', type=FileType('w', encoding='utf-8'),
            metavar='FILE', help='File to write the TeX code to')
    p_output.add_argument('--pdf',
            metavar='FILE', help='The output PDF file')

    p_misc = parser.add_argument_group('Miscellaneous options')
    p_misc.add_argument('--large-text', dest='large_text', action='store_true',
            help='Use large font and more line spacing for text')
    p_misc.add_argument('--exclude-voca', dest='include_voca', action='store_false',
            help='Do not generate any vocabulary lists')
    p_misc.add_argument('--combine-voca', action='store_true',
            help='Use one vocabulary list for all passages')
    p_misc.add_argument('--clearpage-before-voca', action='store_true',
            help='Start a new page before vocabulary lists')

    parser.add_argument('passages', metavar='PASSAGE', nargs='*',
            help=textwrap.dedent('''\
            The passages to include.
            Examples of correct input are:\n
            - Psalms 1
            - Exodus 3:15
            - Genesis 1-2:3
            - Genesis 2:4-11 (NB: the 11 is the chapter!)
            - 1 Kings 17:7-end
            - Job 38:1-bookend'''))

    args = parser.parse_args()

    if args.tex is None:
        tex = tempfile.mkstemp(suffix='.tex', prefix='reader')
        args.tex = open(tex[1], 'w', encoding='utf-8')
    if args.pdf is None:
        args.pdf = tempfile.mkstemp(suffix='.pdf', prefix='reader')[1]

    print('Loading data...')
    load_verse_nodes()

    if len(args.passages) == 0:
        print('No passages given, not doing anything')
        return

    print('Generating reader...')
    try:
        templates = {}
        templates['pre'] = args.pre_tex.read()
        templates['post'] = args.post_tex.read()
        templates['pretext'] = args.pre_text_tex.read()
        templates['posttext'] = args.post_text_tex.read()
        templates['prevoca'] = args.pre_voca_tex.read()
        templates['postvoca'] = args.post_voca_tex.read()
        generate(args.passages,
                args.include_voca, args.combine_voca, args.clearpage_before_voca,
                args.large_text, False, args.tex, args.pdf, args.lang, templates)
    except Exception as e:
        print(e)
        sys.exit(1)

    print('Output written to', args.pdf)

if __name__ == '__main__':
    main()
