"""Bounded glyph geometry recovery anchored by an observed colored card.

Only enriched native-role records enter this path. Background bounds establish
one coordinate system for color and badge checks; glyph scale/translation never
moves the badge. No expected identity or renderer coordinates are inputs.
"""
from collections import OrderedDict
import cv2
import numpy as np

SCALES = tuple(np.linspace(.90, 1.10, 9))
MIN_GLYPH = .88
MIN_BACKGROUND = .75
MIN_CORNER = .45
MIN_MARGIN = .03
_CACHE = OrderedDict()
_CACHE_LIMIT = 128


def _white(image):
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    feature = ((hsv[:, :, 1] <= 60) & (hsv[:, :, 2] >= 170)).astype(np.uint8) * 255
    return cv2.GaussianBlur(feature, (3, 3), .5)


def _ncc(a, b):
    if a.size < 100 or a.shape != b.shape:
        return None
    a, b = a.astype(np.float32).ravel(), b.astype(np.float32).ravel()
    a -= a.mean(); b -= b.mean()
    divisor = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / divisor) if divisor > 1e-6 else None


def _describe(record):
    source = record['bgr']; key = id(source)
    if key in _CACHE and _CACHE[key]['source'] is source:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    meta = record['meta']
    layout=meta.get('layout')
    if not isinstance(layout,dict) or not isinstance(layout.get('glyph'),dict): return None
    center=layout['glyph'].get('center')
    if (not isinstance(center,(list,tuple)) or len(center)!=2
            or any(type(v) not in (int,float) or not np.isfinite(v) for v in center)
            or not all(0<=v<=max(source.shape[:2]) for v in center)): return None
    x1,y1,x2,y2 = meta['glyph_region']
    white = _white(source)
    glyph = white[y1:y2,x1:x2]
    ys,xs = np.where(glyph > 100)
    if len(xs) < 100:
        return None
    left,top,right,bottom = int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)
    glyph = glyph[top:bottom,left:right]
    variants = []
    for scale in SCALES:
        w,h = max(2,round(glyph.shape[1]*scale)),max(2,round(glyph.shape[0]*scale))
        variants.append((float(scale), cv2.resize(glyph,(w,h),interpolation=cv2.INTER_LINEAR)))
    hsv=cv2.cvtColor(source,cv2.COLOR_BGR2HSV)
    background=cv2.erode(np.uint8((hsv[:,:,1]>70)&(hsv[:,:,2]>60)),np.ones((5,5),np.uint8)).astype(bool)
    if meta['corner']:
        a,b,c,d=meta['corner_region']; background[b:d,a:c]=False
    value={'source':source,'white':white,'variants':variants,'bbox':(left,top,right,bottom),
           'background':background,'hue':hsv[:,:,0], 'center':meta['layout']['glyph']['center']}
    _CACHE[key]=value; _CACHE.move_to_end(key)
    while len(_CACHE)>_CACHE_LIMIT: _CACHE.popitem(last=False)
    return value


def _boxes(image):
    hsv=cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
    mask=np.uint8((hsv[:,:,1]>70)&(hsv[:,:,2]>60))
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((7,7),np.uint8))
    count,labels,stats,centers=cv2.connectedComponentsWithStats(mask)
    result=[]
    for x,y,w,h,area in stats[1:]:
        if 115<=w<=185 and 115<=h<=205 and area>=w*h*.30:
            result.append((int(x),int(y),int(w),int(h),int(area)))
    return sorted(result,key=lambda row:-row[4])[:2]


def _glyph_score(observed, descriptor):
    # Search only near the measured background anchor, rather than anywhere
    # else in the captured card. The full field remains available for clipping.
    left,top,right,bottom=descriptor['bbox']
    best=None
    center_x=(left+right)/2; center_y=(top+bottom)/2
    glyph_center=descriptor['center']
    for scale,template in descriptor['variants']:
        # Relative displacement from the normal glyph center is bounded. Pink
        # center differences are absorbed by the small translation search.
        expected_x=center_x*scale+glyph_center[0]*(1-scale)-template.shape[1]/2
        expected_y=center_y*scale+glyph_center[1]*(1-scale)-template.shape[0]/2
        x0=max(0,int(np.floor(expected_x))-8); y0=max(0,int(np.floor(expected_y))-8)
        x2=min(observed.shape[1],int(np.ceil(expected_x))+template.shape[1]+9)
        y2=min(observed.shape[0],int(np.ceil(expected_y))+template.shape[0]+9)
        field=observed[y0:y2,x0:x2]
        if field.shape[0]<template.shape[0] or field.shape[1]<template.shape[1]: continue
        scores=cv2.matchTemplate(field,template,cv2.TM_CCOEFF_NORMED)
        finite=np.isfinite(scores)&(scores>=-1.000001)&(scores<=1.000001)
        if not finite.any(): continue
        scores=np.where(finite,scores,-np.inf)
        yy,xx=np.unravel_index(np.argmax(scores),scores.shape)
        score=float(scores[yy,xx])
        if best is None or score>best['score']:
            best={'score':score,'scale':scale,'translation':[float(x0+xx-expected_x),float(y0+yy-expected_y)]}
    return best


def match_geometric_card(image, records, allowed, *, badge_model=None):
    """Return (PID filename or None, correlation score, measured diagnostics)."""
    diagnostics={'accepted':False,'reason':'geometry_unavailable','candidates':[], 'card_boxes':[],
                 'score_basis':'geometric_glyph_ncc'}
    if allowed is None or not allowed or not isinstance(image,np.ndarray) or image.dtype!=np.uint8 or image.ndim!=3 or image.shape[2] not in (3,4) or not image.size:
        return None,0.,diagnostics
    from src.card_matcher import _valid_enriched
    import re
    records=[record for record in records if isinstance(record,dict)
             and re.fullmatch(r'\d+\.png', str(record.get('filename','')))
             and record['filename'].removesuffix('.png') in allowed and _valid_enriched(record)]
    if not records: return None,0.,diagnostics
    image=image[:,:,:3]
    boxes=_boxes(image); diagnostics['card_boxes']=[list(box[:4]) for box in boxes]
    if not boxes: return None,0.,diagnostics
    rows=[]; patch_cache={}; glyph_cache={}; badge_cache={}
    for record in records:
        meta=record['meta']
        if not meta.get('layout') or not meta.get('glyph_region') or record['filename'].removesuffix('.png') not in allowed: continue
        descriptor=_describe(record)
        if descriptor is None: continue
        height,width=record['gray'].shape
        queries=[]
        for bx,by,bw,bh,area in boxes:
            for adjust in (-2,0,2):
                qw=bw+adjust; qh=round(height*qw/width)
                if (abs(bh-qh)>8 or bx+qw>image.shape[1] or by+qh>image.shape[0]): continue
                queries.append((bx,by,qw,qh))
        for bx,by,bw,bh in queries:
            key=(bx,by,bw,bh,height,width)
            if key not in patch_cache:
                patch=cv2.resize(image[by:by+bh,bx:bx+bw],(width,height),interpolation=cv2.INTER_LINEAR)
                hsv=cv2.cvtColor(patch,cv2.COLOR_BGR2HSV)
                patch_cache[key]=(patch,hsv,_white(patch),cv2.cvtColor(patch,cv2.COLOR_BGR2GRAY))
            patch,hsv,white,gray=patch_cache[key]
            observed_background=cv2.erode(np.uint8((hsv[:,:,1]>70)&(hsv[:,:,2]>60)),np.ones((5,5),np.uint8)).astype(bool)
            background=descriptor['background']&observed_background
            if background.sum()<500: continue
            actual_hue=float(np.median(hsv[:,:,0][background])); expected_hue=float(np.median(descriptor['hue'][background]))
            hue=abs(actual_hue-expected_hue); hue=min(hue,180-hue)
            if hue>18: continue
            bg_score=_ncc(gray[background],record['gray'][background])
            if bg_score is None or bg_score<MIN_BACKGROUND: continue
            glyph_key=(key,meta['glyph'],meta['stype'],meta['rarity'])
            if glyph_key not in glyph_cache:
                observed_glyph=white.copy()
                if meta['corner']:
                    a,b,c,d=meta['corner_region']; observed_glyph[b:d,a:c]=0
                glyph_cache[glyph_key]=_glyph_score(observed_glyph,descriptor)
            glyph=glyph_cache[glyph_key]
            if glyph is None: continue
            corner=None
            if meta['corner']:
                a,b,c,d=meta['corner_region']; corner=_ncc(gray[b:d,a:c],record['gray'][b:d,a:c])
            component_valid=not meta['corner'] or (corner is not None and corner>=MIN_CORNER)
            badge_evidence=None; badge_score=corner
            if badge_model is not None and meta['corner'] and glyph['score']>=MIN_GLYPH:
                badge_key=(key,tuple(meta['corner_region']))
                if badge_key not in badge_cache:
                    try:
                        prediction=badge_model.classify_badge(patch,{'corner_region':meta['corner_region']})
                        probs=np.asarray(prediction.get('probs',[]),dtype=np.float64)
                        if (prediction.get('valid') and probs.shape==(4,) and np.all(np.isfinite(probs))
                                and np.all(probs>=0) and np.all(probs<=1) and np.isclose(probs.sum(),1,atol=1e-4)
                                and np.max(probs)>=.95 and np.sort(probs)[-1]-np.sort(probs)[-2]>=.50):
                            badge_cache[badge_key]={'corner':int(np.argmax(probs)), 'confidence':float(np.max(probs))}
                        else: badge_cache[badge_key]=None
                    except (ValueError,TypeError,KeyError,AttributeError,cv2.error,FloatingPointError):
                        badge_cache[badge_key]=None
                badge_evidence=badge_cache[badge_key]
                if badge_evidence is not None:
                    component_valid=badge_evidence['corner']==meta['corner'] and badge_evidence['corner']!=0
                    badge_score=1. if component_valid else 0.
            rank=.75*glyph['score']+.25*(badge_score if badge_score is not None else bg_score)
            rows.append({'filename':record['filename'],'glyph_score':glyph['score'],'background_score':bg_score,
                         'corner_score':corner,'badge_network':badge_evidence,'hue_distance':hue,'ranking_score':rank,'score':glyph['score'],
                         'component_valid':component_valid,'location':[bx,by],'card_size':[bw,bh],
                         'glyph_scale':glyph['scale'],'glyph_translation':glyph['translation'],
                         'family':(meta['glyph'],meta['stype'],meta['rarity'])})
    # Each ID competes once; transforms of the same identity are not runner-ups.
    ranked={}
    for row in rows:
        if row['filename'] not in ranked or row['ranking_score']>ranked[row['filename']]['ranking_score']:
            ranked[row['filename']]=row
    rows=sorted(ranked.values(),key=lambda row:(-row['ranking_score'],row['filename']))
    diagnostics['candidates']=rows[:5]
    valid=[row for row in rows if row['component_valid'] and row['glyph_score']>=MIN_GLYPH]
    if not valid:
        diagnostics['reason']='geometry_component_mismatch'
        return None,rows[0]['score'] if rows else 0.,diagnostics
    best=valid[0]; diagnostics['best']=best
    lookup={record['filename']:record for record in records}; winning=lookup[best['filename']]['bgr']
    identical=[record['filename'] for record in records if record['bgr'].shape==winning.shape and np.array_equal(record['bgr'],winning)]
    if len(identical)>1:
        diagnostics.update(reason='identical_pixels',ambiguous_ids=identical)
        return None,best['score'],diagnostics
    contenders=[row for row in rows if row['filename']!=best['filename'] and row['glyph_score']>=.80]
    margin=best['ranking_score']-contenders[0]['ranking_score'] if contenders else 1.
    diagnostics['margin']=float(margin)
    if margin<MIN_MARGIN:
        diagnostics['reason']='insufficient_geometry_margin'
        return None,best['score'],diagnostics
    diagnostics.update(accepted=True,reason='accepted_geometric_alignment')
    return best['filename'],best['score'],diagnostics
