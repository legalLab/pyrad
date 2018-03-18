#!/usr/bin/env python2

import numpy as np
import os
import shutil
import sys
import glob
import subprocess
import multiprocessing
import gzip

from itertools import izip, chain, groupby, takewhile
from copy import copy
from potpour import *
from consensdp import unhetero, uplow, breakalleles
from cluster_cons7_shuf import comp

import loci2phynex
import loci2vcf
import loci2treemix
import loci2SNP
import loci2mig
import loci2gphocs
import alleles2mig
import alleles2phynex


def unstruct(amb):
    amb = amb.upper()
    " returns bases from ambiguity code"
    D = {"R":["G","A"],
         "K":["G","T"],
         "S":["G","C"],
         "Y":["T","C"],
         "W":["T","A"],
         "M":["C","A"],
         "A":["A","A"],
         "T":["T","T"],
         "G":["G","G"],
         "C":["C","C"],
         "N":["N","N"],
         "-":["-","-"]}
    return D.get(amb)


def most_common(L):
    return max(groupby(sorted(L)), key=lambda(x, v):(len(list(v)),-L.index(x)))[0]


def stack(D):
    """
    from list of bases at a site D,
    returns an ordered list of counts of bases
    """
    L = len(D)
    counts = []
    for i in range(len(D[0])):
        R=Y=S=W=K=M=0
        for nseq in range(L):
            R += D[nseq][i].count("R")
            Y += D[nseq][i].count("Y")
            S += D[nseq][i].count("S")
            W += D[nseq][i].count("W")
            K += D[nseq][i].count("K")
            M += D[nseq][i].count("M")
        counts.append( [R,Y,S,W,K,M] )
    return counts


def countpolys(seqs):
    t = [tuple([i.upper() for i in seq]) for seq in seqs]
    return max([sum(i) for i in stack(t)])


def alignfast(WORK,pronum,names,seqs,muscle):
    """
    if ST is very large it needs to be written to file, otherwise
    the process can just be piped
    """
    ST = "\n".join('>'+i+'\n'+j[0] for i,j in zip(names,seqs))
    if len(ST) > 100000:
        fstring = WORK+".tempalign_"+pronum
        with open(fstring,'w') as inST:
            print >>inST, ST
        cmd = muscle+" -quiet -in "+fstring  #+" -out "+ostring
    else:
        cmd = "/bin/echo '"+ST+"' | "+muscle+" -quiet -in -"

    fout = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
    ff = fout.stdout.read()
    return ff


def polyaccept(maxpoly, MAXpoly, ln):
    passed = 1
    if 'p' in str(MAXpoly):
        if maxpoly > ln*float(MAXpoly.replace('p','')):
            passed = 0
    else:
        MAXpoly = int(MAXpoly)
        if maxpoly > MAXpoly:
            passed = 0
    if passed:
        return 1


def sortalign(stringnames):
    G = stringnames.split("\n>")
    GG = [i.split("\n")[0].replace(">","")+"\n"+"".join(i.split('\n')[1:]) for i in G]
    aligned = [i.split("\n") for i in GG]
    aligned.sort(key=lambda x: int(x[0].split("_")[-1]))
    nn = [">"+i[0] for i in aligned]
    seqs = [i[1] for i in aligned]
    return nn,seqs


def trimmer(overhang, nn, sss, datatype, minspecies):
    " overhang trim or keep "
    FM1 = SM1 = None
    FM2 = SM2 = None

    " only trim if more than three samples "
    mintcov = 4
    if minspecies < 4:
        mintcov = minspecies
    
    if minspecies > 1:
        if 'pair' in datatype:
            firsts = [i.split("n")[0] for i in sss]
            seconds = [i.split("n")[-1] for i in sss]

            "treat each read separately, or do the total read?"
            if len(overhang) == 4:
                a,b,c,d = overhang
            elif len(overhang) == 2:
                a,c = overhang
                b=a; d=c

            "trim 1st read "
            leftlimit = [FF(i,'min') for i in firsts]
            rightlimit = [FF(i,'max') for i in firsts]

            " trim1 means at least four samples have data at that site"
            " trim2 means that all samples with data have data at that site"
            if a == 1:
                " trim1 1st left overhang "
                FM1 = min([i for i in xrange(len(firsts[0])) if [j<=i for j in leftlimit].count(True) >= mintcov])
            elif a == 2:
                try: FM1 = min([i for i in xrange(len(firsts[0])) if [j<=i for j in leftlimit].count(True) == len(nn)])
                except ValueError: FM1 = 1
                        
            if b == 1:
                " trim 1st right overhang "
                SM1 = max([i for i in xrange(len(firsts[0])) if [j>=i for j in rightlimit].count(True) >= mintcov])
            elif b == 2:
                try: SM1 = max([i for i in xrange(len(firsts[0])) if [j>=i for j in rightlimit].count(True) == len(nn)])
                except ValueError: SM1 = 1

            "trim 2nd read "
            leftlimit = [FF(i,'min') for i in seconds]
            rightlimit = [FF(i,'max') for i in seconds]

            if c == 1:
                " trim1 2nd left overhang "
                try: FM2 = min([i for i in xrange(len(seconds[0])) if [j<=i for j in leftlimit].count(True) >= mintcov])
                except ValueError:
                    " no sites where 4 samples have data "
                    FM2 = 1
                    empty2 = 1
                        
            elif c == 2:
                " trim1 2nd left overhang "
                try: FM2 = min([i for i in xrange(len(seconds[0])) if [j<=i for j in leftlimit].count(True) == len(nn)])
                except ValueError:
                    " no sites where all samples have data "
                    FM2 = 1
                    empty2 = 1

            if d == 1:
                " trim 2nd right overhang "
                SM2 = max([i for i in xrange(len(seconds[0])) if [j>=i for j in rightlimit].count(True) >= mintcov])
            elif d == 2:
                SM2 = max([i for i in xrange(len(seconds[0])) if [j>=i for j in rightlimit].count(True) == len(nn)])+1

            "put pair back together"
            sss=[i+"nnnn"+j for i,j in zip(firsts,seconds)]
                        
        else:
            leftlimit = [FF(i,'min') for i in sss]
            rightlimit = [FF(i,'max') for i in sss]

            " trim left overhang "
            if overhang[0] == 1:
                FM1 = min([i for i in xrange(len(sss[0])) if [j<=i for j in leftlimit].count(True) > 3])
            elif overhang[0] == 2:
                FM1 = max(leftlimit)
            else:
                FM1 = min(leftlimit)
                        
            " trim right overhang "
            if overhang[1] == 1:
                SM1 = max([i for i in xrange(len(sss[0])) if [j>=i for j in rightlimit].count(True) > 3])+1
            elif overhang[1] == 2:
                SM1 = min(rightlimit)+1
            else:
                SM1 = max(rightlimit)+1
    return [FM1, FM2, SM1, SM2]



def lowerin(seqs, sss):
    ## slow kludge code to put lower case ambigs back in
    ## todo: speed up
    ambigs = []
    for seq in seqs:
        ambigs.append([i for i in seq[0] if i in "RSMKYWrsmkyw"])
 
    ## put lowers in 
    for idx in range(len(seqs)):
        ## which base needs replacing
        dambd = {i:0 for i in "RSMKYW"}
        row = ambigs[idx]

        for amb in row:
            dambd[amb.upper()] += 1
            sss[idx] = sss[idx].replace(amb.upper(), amb, dambd[amb.upper()])
    return sss




def alignFUNC(infile, minspecies, ingroup,
              MAXpoly, outname, s1, s2,
              muscle, exclude,
              overhang, WORK,
              CUT, a1, a2, datatype, longname, makealleles):

    " TODO: resolve ambiguous cutters "
    if "," in CUT:
        CUT1,CUT2 = CUT.split(",")
    else:
        CUT1 = CUT
        CUT2 = CUT1

    " open clust file "
    f = open(infile)
    " assign number to files for this process "
    pronum = str("".join(infile.split("_")[-1]))    
    " create temp out files for aligned clusters "
    aout = open(WORK+".align_"+pronum,'w')
    nout = open(WORK+".not_"+pronum,'w')
    " create counters "
    locus = paralog = g4 = dups = 0
    " read in clust file 2 lines at a time"
    k = izip(*[iter(f)]*2)
    while 1:
        D = P = S = I = notes = ""
        try:
            d = k.next()
        except StopIteration:
            break
        locus += 1
        names = []    ## record names w/ number
        cnames = []   ## record names w/o number
        onames = []   ## record meta info for locus
        seqs = []
        nameiter = 0
        while "//\n" not in d[0]:
            "record names and seqs, remove # at end"
            "record the name into locus name. "
            nam = d[0][1:].rsplit("_", 2)[0]
            
            if nam not in exclude:
                cnames.append(nam)
                names.append(nam+"_"+str(nameiter))
                onames.append("_".join(d[0][1:].rsplit("_", 2)[1:]))
                seqs.append(d[1].strip())
            d = k.next()
            nameiter += 1

        ## get loc number
        #notes = d[0].split("//")[1]
       
        " apply duplicate filter "
        if len(cnames) != len(set(cnames)):      ## no grouping un-clustered copies from same taxon
            dups += 1                            ## record duplicates in loci
            D = '%D'
            
        " apply minsamp filter "
        if len([i for i in cnames if i in ingroup])>=minspecies:  ## too few ingroup samples in locus
            g4 += 1

            "align read1 separate from read2"
            if 'pair' in datatype:
                " compatibility from pyrad 2 -> 3 "
                SEQs = [i.replace("X",'n') for i in seqs]
                firsts  = [[i.split("nnnn")[0]] for i in SEQs]
                seconds = [[i.split("nnnn")[-1]] for i in SEQs]

                "align first reads"
                stringnames = alignfast(WORK,pronum,names,firsts,muscle)
                nn, ss1 = sortalign(stringnames)
                if makealleles:
                    ss1 = lowerin(firsts, ss1)

                D1 = {}
                for i in range(len(nn)):
                    D1[nn[i]] = ss1[i]
                "reorder keys by name"
                keys = D1.keys()
                keys.sort(key=lambda x:int(x.split("_")[-1]),reverse=True)

                "align second reads"
                stringnames = alignfast(WORK,pronum,names,seconds,muscle)
                nn, ss2 = sortalign(stringnames)
                if makealleles:
                    ss2 = lowerin(seconds, ss2)

                D2 = {}
                for i in range(len(nn)):
                    D2[nn[i]] = ss2[i]
                nn = keys 
                sss = [D1[key]+"nnnn"+D2[key] for key in keys]
                
            else:
                "align reads"
                seqs = [[i] for i in seqs]

                ## get alignment
                stringnames = alignfast(WORK,pronum,names,seqs,muscle)
                if len(stringnames) < 1:
                    print stringnames
                ## sort alignment
                nn, sss = sortalign(stringnames)
                ## put lowers in 
                if makealleles:
                    sss = lowerin(seqs, sss)


            " now strip off cut sites "
            if datatype == "merged":
                sss = [i[len(CUT1):-len(CUT2)] for i in sss]
            elif ("c1" in onames) or ("pair" in onames):
                sss = [i[len(CUT1):-len(CUT2)] for i in sss]
            else:
                sss = [i[len(CUT1):] for i in sss]

            " apply number of shared heteros paralog filter "
            nn = ["_".join(i.split("_")[:-1]) for i in nn]
            maxpoly = countpolys(sss)               
            if not D:
                " apply paralog filter "
                if not polyaccept(maxpoly, MAXpoly, len(nn)):
                    P = '%P'

            zz = zip(nn,sss)

            " record variable sites "
            bases = []
            for i in range(len(sss[0])):      ## create list of bases at each site
                site = [s[i] for s in sss]
                bases.append(site)
            basenumber = 0
            snpsite = [" "]*len(sss[0])

            " put in split for pairs "
            for i in range(len(sss[0])):
                if sss[0][i] == "n":
                    snpsite[i] = 'n'

            " record a string for variable sites in snpsite"
            for site in bases:
                reals = [i.upper() for i in site if i not in list("N-")]
                if len(set(reals)) > 1:                                ## if site is variable
                    " convert ambiguity bases to reals "
                    for i in xrange(len(reals)):
                        if reals[i] in list("RWMSYK"):
                            for j in unstruct(reals[i]):
                                reals.append(j)
                    reals = [i for i in reals if i not in list("RWMSYK")]
                    if sorted([reals.count(i) for i in set(reals)], reverse=True)[1] > 1:  # not autapomorphy
                        snpsite[basenumber] = "*"                      ## mark PIS for outfile .align
                    else:                                              ## if autapormorphy
                        snpsite[basenumber] = '-'
                basenumber += 1

            " get trimmed edges "
            FM1,FM2,SM1,SM2 = trimmer(overhang,nn,sss,datatype, minspecies)

            "alphabetize names"
            zz.sort()

            " filter for duplicates or paralogs, then SNPs and Indels "
            if not (D or P):

                " SNP filter "
                if 'pair' in datatype:
                    snp1, snp2 = "".join(snpsite).split("nnnn")
                    snp1 = snp1.replace("*","-")
                    if snp1.count("-") > int(s1):
                        S = "%S1"
                    else:
                        snp2 = snp2.replace("*","-")
                        if snp2.count("-") > int(s2):
                            S = "%S2"
                else:
                    if ("".join(snpsite[FM1:SM1]).replace("*","-").count('-') > int(s1)):
                        S = "%S"

                " indel filter"
                if not S:
                    if "pair" in datatype:
                        spacer = sss[0].index("n")
                        if any([y[FM1:spacer].count("-") > int(a1) for x,y in zz]):
                            I = "%I1"
                        elif any([y[spacer:SM2].count("-") > int(a2) for x,y in zz]):
                            I = "%I2"
                    else:
                        if any([y[FM1:SM1].count("-") > int(a1) for x,y in zz]):
                            I = "%I"

                " final check of edge filter "
                if not SM1-FM1 >= 32:
                    S = "%S"
                

            if len(D+P+S+I) == 0:
                " write aligned loci to temp files for later concatenation into the .loci file"
                if 'pair' in datatype:
                    snp1,snp2 = "".join(snpsite).split("nnnn")
                    for x, y in zz:
                        first,second = y.split("nnnn")
                        space = ((longname+5)-len(x))
                        print >>aout, "{}{}{}nnnn{}".format(x, " "*space, first[FM1:SM1], second[FM2:SM2])
                    print >>aout, "//{}{}    {}|{}".format(" "*(longname+3), snp1[FM1:SM1], snp2[FM2:SM2], notes)
                else:
                    for x, y in zz:
                        space = ((longname+5)-len(x))
                        print >>aout, "{}{}{}".format(x, " "*space, y[FM1:SM1])
                    print >>aout, "//{}{}|{}".format(" "*(longname+3), "".join(snpsite[FM1:SM1]), notes)
                    
            else:
                " write to exclude file "
                if 'pair' in datatype:
                    snp1,snp2 = "".join(snpsite).split("nnnn")
                    for x,y in zz:
                        first,second = y.split("nnnn")
                        space = ((longname+5)-len(x))
                        print >>nout, "{}{}{}nnnn{}".format(x, " "*space, first[FM1:SM1].upper(), second[FM2:SM2].upper())
                    print >>nout, "//{}{}{}    {}|{}".format(D+P+S+I, " "*(longname+3-len(D+P+S+I)),
                                                               snp1[FM1:SM1], snp2[FM2:SM2], notes)

                else:
                    for x, y in zz:
                        space = ((longname+5)-len(x))
                        print >>nout, "{}{}{}".format(x, " "*space, y[FM1:SM1].upper())
                    print >>nout, "//{}{}|{}".format(D+P+S+I, " "*(longname+3-len(D+P+S+I)),
                                                      "".join(snpsite[FM1:SM1]), notes)

                                    
    nout.close()
    aout.close()
    sys.stderr.write('.')
    return locus 



def FF(x,minmax):
    " finds leftmost or rightmost base in an alignment "
    if minmax == 'max':
        #try: ff = max([i for i,j in enumerate(x) if j not in ['-',"N"]])
        try: ff = max([i for i,j in enumerate(x) if j != "-"])
        except ValueError:
            ff = 1

    elif minmax == 'min':
        #try: ff = min([i for i,j in enumerate(x) if j not in ['-',"N"]])
        try: ff = min([i for i,j in enumerate(x) if j != "-"])
        except ValueError:
            " only Ns and -s"
            ff = len(x)
    return ff



def blocks(files, size=2048000):
    """ read in blocks 2Mb at a time for speed """
    while True:
        b = files.read(size)
        if not b:
            break
        yield b


def splitandalign(ingroup, minspecies, outname, infile,
              MAXpoly, parallel, s1, s2, muscle,
              exclude, overhang, WORK, CUT,
              a1, a2, datatype, longname, nloci, formats):

    """ split cluster file into smaller files depending on the number
    of processors and align each file separately using alignfunc function."""

    ## double check that old chunk and aligns are removed
    for i in glob.glob(WORK+".align*"):
        os.remove(i)
    for i in glob.glob(WORK+".chunk*"):
        os.remove(i)

    ## read infile, split into chunks for aligning, nchuncks
    ## depends on number of available processors
    data = gzip.open(infile, 'rb').read().strip().split("//\n")
    minpar = max(3, parallel)  ## pp
    chunks = [0+(len(data)/minpar)*i for i in range(minpar)]

    for i in range(len(chunks)-1):
        with open(WORK+".chunk_"+str(i), 'w') as dat:
            dat.write("//\n//\n".join(data[chunks[i]:chunks[i+1]])+"//\n//\n")

    ## write the last chunk
    with open(WORK+".chunk_"+str(i+1), 'w') as dat:
        dat.write("//\n//\n".join(data[chunks[i+1]:])+"//\n//\n")

    ## make alleles file
    makealleles = bool("a" in formats)

    ## set up parallel
    work_queue = multiprocessing.Queue()
    result_queue = multiprocessing.Queue()
    for handle in glob.glob(WORK+".chunk*"):
        #work_queue.put([params, handle, ingroup, 
        #                exclude, longname, quiet])
        work_queue.put([handle, minspecies, ingroup, MAXpoly,
                        outname, s1, s2, muscle, 
                        exclude, overhang, WORK, CUT,
                        a1, a2, datatype, longname, makealleles])
    ## spawn workers
    jobs = []
    for i in range(minpar):
        worker = Worker(work_queue, result_queue, alignFUNC)
        jobs.append(worker)
        worker.start()
    for j in jobs:
        j.join()

    locus = 0
    for handle in glob.glob(WORK+".chunk*"):
        locus += int(result_queue.get())

    " output loci and excluded loci and delete temp files... "
    locicounter = 1
    aligns = glob.glob(WORK+".align*")
    aligns.sort(key=lambda x: int(x.split("_")[-1]))

    ## write loci output
    locifile = open(WORK+"outfiles/"+outname+".loci", "w")    
    for chunkfile in aligns:
        chunkdata = open(chunkfile, "r")
        for line in chunkdata:
            if line.startswith("//"):
                #line = line.replace("|\n", str(locicounter)+"|\n", 1)
                #lines = lines.replace("\n", str(locicounter)+"\n", 1)
                locifile.write("{}{}|\n".format(line.strip(), locicounter))
                locicounter += 1
            else:
                nam_, seq = line.rsplit(" ", 1)
                locifile.write("{} {}".format(nam_, seq.upper()))
        chunkdata.close()
    locifile.close()


    if makealleles:    
        locicounter = 1    
        ## write alleles output
        allelesfile = open(WORK+"outfiles/"+outname+".alleles", "w")        
        for chunkfile in aligns:
            chunkdata = open(chunkfile, "r")
            for line in chunkdata:
                if line.startswith("//"):
                    allelesfile.write("{}{}|\n".format(line.strip(), locicounter))
                    locicounter += 1
                else:
                    bits = line.split(" ")
                    hap0, hap1 = breakalleles(bits[-1])
                    allelesfile.write("{}_0{}{}".format(bits[0], " ".join(bits[1:-1]), hap0))
                    allelesfile.write("{}_1{}{}".format(bits[0], " ".join(bits[1:-1]), hap1))
            chunkdata.close()
        allelesfile.close()

    ## clean up chunks and aligns
    chunks = glob.glob(WORK+".chunk*") + glob.glob(WORK+".align")
    for handle in chunks:
        os.remove(handle)
    
    unaligns = glob.glob(WORK+".not*")
    excluded_loci_file = open(WORK+"outfiles/"+outname+".excluded_loci", "w")
    
    for excludechunk in unaligns:
        excludedata = open(excludechunk, "r")
        for lines in excludedata:
            excluded_loci_file.write(lines)
        excludedata.close()
        os.remove(excludechunk)
    
    excluded_loci_file.close()

        
        

def makealign(ingroup, minspecies, outname, infile,
              MAXpoly, parallel, s1, s2, muscle,
              exclude, overhang, WORK, CUT,
              a1, a2, datatype, longname, nloci):

    ## ensure aligns and chunks are removed
    removed = glob.glob(WORK+".align") + glob.glob(WORK+".chunk")
    for infile in removed:
        pass#os.remove(infile)

    ## read infile, split into chunks for aligning, nchuncks
    ## depends on number of available processors """
    with gzip.open(infile, 'rb') as f:
        #totlines = sum(b1.count("\n") for b1 in blocks(f))
        totclust = sum(b1.count("//\n") for b1 in blocks(f))

    f = iter(gzip.open(infile,'rb'))    

    ## split into as many processors ,
    ## or X as many processors if very large
    pp = max(3, parallel)
    done = 0
    chunks = 0
    bigs = (totclust/pp)/2
    sumloci = 0

    while not done:
        nloci = 0
        dat = []
        ## continue to the end of next locus
        gg = takewhile(lambda x: x!="//\n", f)
        while nloci <= bigs:
            try:
                line = next(gg)
                if line:
                    dat.append(line)

            except StopIteration:
                dat.append("//"+str(sumloci+nloci+1)+"\n//\n")
                ## reset generator
                gg = takewhile(lambda x: x!="//\n", f)
                #line = gg.next()
                nloci += 1
                if sumloci+nloci > totclust:
                    done = 'done'
                    break
        sumloci += nloci

        if sumloci < totclust:
            #loci = "".join(dat).split("//\n") 
            with open(WORK+".chunk_"+str(chunks), 'wb') as ff:
                #ff.write("//\n\n".join(loci))
                ff.write("".join(dat))    
            chunks += 1
            #print nloci, sumloci, totclust
    ## final
    loci = "".join(dat).split("//\n") #[:-1]
    with open(WORK+".chunk_"+str(chunks), 'wb') as ff:
        ff.write("//\n\n".join(loci))     #+"//\n\n")
    chunks += 1
    #print nloci, sumloci, totclust
        
    " set up parallel "
    work_queue = multiprocessing.Queue()
    result_queue = multiprocessing.Queue()
    for handle in sorted(glob.glob(WORK+".chunk*")):
        work_queue.put([handle, minspecies, ingroup, MAXpoly,
                        outname, s1, s2, muscle, 
                        exclude, overhang, WORK, CUT,
                        a1, a2, datatype, longname])

    " spawn workers "
    jobs = []
    for i in range(pp):
        worker = Worker(work_queue, result_queue, alignFUNC)
        jobs.append(worker)
        worker.start()
    for j in jobs:
        j.join()

    #print("done with that")
    
    locus = 0
    for handle in glob.glob(WORK+".chunk*"):
        locus += int(result_queue.get())

    " output loci and excluded loci and delete temp files... "
    locicounter = 1
    aligns = glob.glob(WORK+".align*")
    aligns.sort(key=lambda x: int(x.split("_")[-1]))
    locifile = open(WORK+"outfiles/"+outname+".loci", "w")
    
    for chunkfile in aligns:
        chunkdata = open(chunkfile, "r")
        for lines in chunkdata:
            if lines.startswith("//"):
                #lines = lines.replace("|\n", "|"+str(locicounter)+"\n", 1)
                #lines = lines.replace("\n", ","+str(locicounter)+"\n", 1)
                lines = lines.replace("\n", str(locicounter)+"\n", 1)                
                locicounter += 1
            locifile.write(lines)
        chunkdata.close()
        #os.remove(chunkfile)

    ## clean up
    for handle in glob.glob(WORK+".chunk*"):
        if os.path.exists(handle):
            os.remove(handle)
    
    locifile.close()
    
    unaligns = glob.glob(WORK+".not*")
    excluded_loci_file = open(WORK+"outfiles/"+outname+".excluded_loci", "w")
    
    for excludechunk in unaligns:
        excludedata = open(excludechunk, "r")
        for lines in excludedata:
            excluded_loci_file.write(lines)
        excludedata.close()
        os.remove(excludechunk)
    
    excluded_loci_file.close()
    

def DoStats(ingroup, outgroups, outname, 
            WORK, minspecies,longname):

    " message to screen "
    print "\n\tfinal stats written to:\n\t "+WORK+"stats/"+outname+".stats"
    print "\toutput files being written to:\n\t "+WORK+"outfiles/ directory\n"

    " open stats file for writing, and loci file for reading "
    statsout  = open(WORK+"stats/"+outname+".stats",'w')   
    finalfile = open(WORK+"outfiles/"+outname+".loci").read() 
    notkept   = open(WORK+"outfiles/"+outname+".excluded_loci").read()

    " get stats from loci and excluded_loci "
    nloci = finalfile.count("|\n")
    npara = notkept.count("%P")
    ndups = notkept.count("%D")
    nMSNP = notkept.count("%S")

    " print header for how many loci are kept  "
    print >>statsout, "\n"
    print >>statsout, str(nloci+npara+nMSNP)+\
          " "*(12-len(str(nloci+npara+nMSNP)))+\
          "## loci with > minsp containing data"
    print >>statsout, str(nloci+nMSNP)+\
          " "*(12-len(str(nloci+nMSNP)))+\
          "## loci with > minsp containing data & paralogs removed"
    print >>statsout, str(nloci)+\
          " "*(12-len(str(nloci)))+\
          "## loci with > minsp containing data & paralogs removed & final filtering\n"

    " print columns for how many loci were found in each sample "
    print >>statsout, "## number of loci recovered in final data set for each taxon."
    names = list(ingroup)+outgroups
    names.sort()
    
    print >>statsout, '\t'.join(['taxon','nloci'])
    for name in names:
        print >>statsout, name+" "*(longname-len(name))+"\t"+str(finalfile.count(">"+name+" "))
        
    print >>statsout, '\n'
    print >>statsout, "## nloci = number of loci with data for exactly ntaxa"
    print >>statsout, "## ntotal = number of loci for which at least ntaxa have data"
    print >>statsout, '\t'.join(['ntaxa','nloci','saved','ntotal'])

    coverage = [i.count(">") for i in finalfile.strip().split("//")[:-1]]
    if not coverage:
        print "\twarning: no loci meet 'min_sample' setting (line 11)\n\tno results written"
        sys.exit()
    coverage.sort()
    print >>statsout, str(1)+"\t-"  
    tot = nloci
    for i in range(2,max(set(coverage))+1):
        if i>=minspecies:
            tot -= coverage.count(i-1)
            print >>statsout, str(i)+"\t"+str(coverage.count(i))+"\t*\t"+str(tot)
        else:
            print >>statsout, str(i)+"\t-\t\t-"
    print >>statsout, "\n"

    " print variable sites counter "
    print >>statsout, "## nvar = number of loci containing n variable sites (pis+autapomorphies)."
    print >>statsout, "## sumvar = sum of variable sites (SNPs)."
    print >>statsout, "## pis = number of loci containing n parsimony informative sites."
    print >>statsout, "## sumpis = sum of parsimony informative sites."    
    print >>statsout, "\t"+'\t'.join(['nvar','sumvar','PIS','sumPIS'])

    #nonpis = [line.count("-") for line in finalfile.split("\n") if "|" in line]
    snps   = [line.count("-")+line.count("*") for line in finalfile.split("\n") if "|" in line]
    pis    = [line.count("*") for line in finalfile.split("\n") if "|" in line]
    zero   = sum([line.count("*")+line.count("-")==0 for line in finalfile.split("\n") if "|" in line])

    print >>statsout, str(0)+"\t"+str(zero)+"\t"+str(0)+"\t"+str(pis.count(0))+"\t"+str(0)
    for i in range(1,max(snps)+1):
        sumvar = sum([(j)*snps.count(j) for j in range(1,i+1)])
        sumpis = sum([(j)*pis.count(j) for j in range(1,i+1)])
        print >>statsout, str(i)+"\t"+str(snps.count(i))+"\t"+str(sumvar)+"\t"+str(pis.count(i))+"\t"+str(sumpis)
    totalvar = sum(snps)#+sum(pis)
    print >>statsout, "total var=",totalvar
    print >>statsout, "total pis=",sum(pis)


    
# def makehaplos(WORK, outname, longname):
#     """
#     TODO print gbs warning that haplos may not be
#     phased on non-overlapping segments.
#     """
#     outfile = open(WORK+"outfiles/"+outname+".alleles", 'w')
#     lines = open(WORK+"outfiles/"+outname+".loci").readlines()
#     writing = []
#     loc = 0
#     for line in lines:
#         if ">" in line:
#             a, b = line.split(" ")[0], line.split(" ")[-1]
#             a1, a2 = breakalleles(b.strip())
#             writing.append(a+"_0"+" "*(longname-len(a)+3)+a1)
#             writing.append(a+"_1"+" "*(longname-len(a)+3)+a2)
#         else:
#             writing.append(line.strip())
#         loc += 1

#         " print every 10K loci "
#         if not loc % 10000:
#             outfile.write("\n".join(writing)+"\n")
#             writing = []
            
#     outfile.write("\n".join(writing))
#     outfile.close()



def cmd_exists(cmd):
    return subprocess.call("type " + cmd, shell=True, 
        stdout=subprocess.PIPE, stderr=subprocess.PIPE) == 0


def main(outgroup, minspecies, outname,
         infile, MAXpoly, parallel,
         maxSNP, muscle, exclude, overhang,
         outform, WORK, gids, CUT,
         a1, a2, datatype, subset,
         version, mindepth, taxadict,
         minhits, seed, ploidy):

    " remove old temp files "
    if glob.glob(WORK+".chunk_*"):
        os.system("/bin/rm "+WORK+".chunk_*")
    if glob.glob(WORK+".align_*"):
        os.system("/bin/rm "+WORK+".align_*")
    if glob.glob(WORK+".not_*"):
        os.system("/bin/rm "+WORK+".not_*")

    " create output directory "
    if not os.path.exists(WORK+'outfiles'):
        os.makedirs(WORK+'outfiles')

    " read names from file and count loci"
    f = iter(gzip.open(infile, 'rb'))
    names = []
    nloci = 0
    ## upper limit for speed
    while nloci < 10000: 
        try:
            line = f.next()
        except StopIteration:
            break
        if ">" in line:
            n = "_".join(line[1:].split("_")[:-2])
            if n not in names:
                names.append(n)
        elif "//" in line:
            nloci += 1
    names = set(names)

    " parse maxSNP argument "
    if 'pair' in datatype:
        if "," in maxSNP:
            s1,s2 = map(int,maxSNP.split(","))
        else:
            s1 = s2 = int(maxSNP)
    else:
        if "," in maxSNP:
            s1 = int(maxSNP[0])
        else:
            s1 = s2 = maxSNP

    " find subset names "
    subset = set([i for i in names if subset in i])

    " remove excludes and outgroups from list "
    if exclude:
        exclude = exclude.strip().split(",")
    else:
        exclude = []
    exclude += list(names.difference(subset))
    if outgroup:
        outgroup = outgroup.strip().split(",")
    else:
        outgroup = []
    for i in exclude:
        names.discard(i)
    ingroup = copy(names)
    for i in outgroup:
        if i in ingroup:
            ingroup.remove(i)


    " print includes and excludes to screen "
    toprint = [i for i in list(ingroup) if i not in exclude]
    toprint.sort()
    print '\tingroup', ",".join(toprint)
    toprint = [i for i in outgroup if i not in exclude]
    toprint.sort()
    print '\taddon', ",".join(toprint)
    print '\texclude', ",".join(exclude)
    print "\t",
    if len(ingroup) <2:
        print "\n\twarning: must have at least two samples selected for inclusion in the data set "
        sys.exit()

    " dont allow more processors than available on machine "
    if parallel > multiprocessing.cpu_count():
        parallel = multiprocessing.cpu_count()

    " find longest name for prettier output files "
    longname = max(map(len, list(ingroup)+list(outgroup)))

    " make other formatted files "
    if "*" in outform:
        outform = ",".join(list("pnasvutmkgf"))
    formats = outform.split(",")

    " check if output files already exist with this outname prefix "
    if os.path.exists(WORK+"outfiles/"+outname+".loci"):
        print "\n\tWarning: data set "+outname+".loci already exists"
        print "\t  Skipping re-alignment. Creating extra data formats from the existing .loci file."
        print "\t  To create a new .loci file and stats output move/delete "+outname+".loci or change"
        print "\t  the outname prefix in the params file\n"

    else:
        " call alignment function to make .loci files"
        locus = splitandalign(ingroup, minspecies, outname, infile,
                          MAXpoly, parallel, s1, s2, muscle,
                          exclude, overhang, WORK, CUT,
                          a1, a2, datatype, longname, nloci, formats)

        " make stats output "
        DoStats(ingroup, outgroup, outname, 
                WORK, minspecies,longname)


    " make phy, nex, SNP, uSNP, structure"
    try:
        os.mkdir(os.path.join(WORK, "tmp"))
        if any([i in formats for i in ['n','p']]):
            if 'p' in formats:
                print "\tfiltering & writing to phylip files"
            if 'n' in formats:
                print "\twriting nexus files"
            loci2phynex.make(WORK,outname,names,longname, formats)
            alleles2phynex.make(WORK,outname,names,longname, formats)
    finally:
        if os.path.exists(os.path.join(WORK, "tmp")):
            shutil.rmtree(os.path.join(WORK, "tmp"))
        

    if 'f' in formats:
        print "\tWriting gphocs file"
        loci2gphocs.make(WORK,outname)

    if any([i in formats for i in ['u','s','k','t','g']]):
        if 's' in formats:
            print "\t  + writing full SNPs file"
        if 'u' in formats:
            print "\t  + writing unlinked bi-allelic SNPs file"
        if 'k' in formats:
            print "\t  + writing STRUCTURE file"            
        if 'g' in formats:
            print "\t  + writing geno file"            
        loci2SNP.make(WORK, outname, names, formats, seed, ploidy)

    " make treemix output "
    if "t" in formats:
        if gids:
            print "\t  + writing treemix file"
            loci2treemix.make(WORK, outname, taxadict, minhits)
        else:
            print "\t  ** must enter group/clade assignments for treemix output "

    " make vcf "
    if 'v' in formats:
        print "\twriting vcf file"
        loci2vcf.make(WORK, version, outname, mindepth, names)
    
    " make alleles output "
    #if "a" in formats:
    #    print "\twriting alleles file"
    #    makehaplos(WORK,outname,longname)
    
    " make migrate output "
    if 'm' in formats:
        if gids:
            print "\twriting migrate-n files"
            loci2mig.make(WORK, outname, taxadict, minhits, seed)
            alleles2mig.make(WORK, outname, taxadict, minhits, seed)
        else:
            print "\t  ** must enter group/clade assignments for migrate-n outputs "

