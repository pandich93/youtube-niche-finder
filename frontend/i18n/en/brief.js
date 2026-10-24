export default {
  // topic verdict
  verdictFree: 'topic is open',
  verdictRecent: 'covered recently',
  verdictProven: 'proven demand',
  verdictFlopped: 'flopped before',

  // brief parts
  partSource: 'Source video',
  partHook: 'Hook',
  partWhyViral: 'Why it took off',
  partTitles: 'Titles',
  partAngle: 'Niche patterns',
  partOverlap: 'Is the topic taken',
  partThumbnailReferences: 'Thumbnail references',

  skippedTitle: 'What could not be collected',
  skippedSub: 'each missing part is named honestly; no templates are filled in instead',

  briefGapTitle: 'Brief for a gap',
  briefOutlierTitle: 'Brief from an outlier',
  briefSub: 'research, not a script: pick YOUR own angle, not a copy',
  gapQuestion: 'Viewer question: <b>{topic}</b>. The topic check and titles are about it; the video below is the source where it was asked.',
  tileMultiplier: 'Multiplier',
  tileViews: 'Views',
  tileFormat: 'Format',
  formatLong: 'long-form',
  seconds: '{s} s',
  tileTopic: 'Topic',
  similarInDb: '{count} similar in your database',
  nicheMeta: ' · niche {niche}',

  hookTitle: 'Hook',
  hookSub: 'first ~75 words of the pasted transcript (about 30 seconds)',

  whyTitle: 'Why it took off',
  whySub: 'LLM analysis based on channel data',
  whyFormula: 'Formula: {text}',
  whyTiming: 'Timing: {text}',

  titlesTitle: 'Titles',
  titlesLlm: 'LLM candidates with scores',
  titlesNoLlm: 'new titles are not generated without the LLM',
  patternSkeletons: 'Phrases that show up more often in niche outliers: {chips}',
  matchedInTitle: 'Already in this title: {chips}',
  bestTime: 'Best time to publish in the niche (your browser’s time): {times}',

  overlapGapTitle: 'Already made on this question',
  overlapTopicTitle: 'Already made on this topic',
  overlapSub: 'excluding the source video',

  thumbRefsTitle: 'Thumbnail references',
  thumbRefsSub: 'similar videos from your database',
  noThumb: 'no thumbnail',
  viewsSimilarity: '{views} views · similarity {pct}%',

  savedDraft: 'Saved to drafts (#{id}). Next — <a href="#/metadata">metadata review</a> and <a href="#/titles">title check</a>.',
  saveDraft: 'Save to drafts',
  addLlm: 'Add LLM analysis (may cost money)',
  saveNote: 'Saving creates a draft linked to this video and queues the missing transcript. No YouTube quota is used.',

  similarThumbsTitle: 'Visually similar',
  similarThumbsSub: 'whose thumbnails look the same — is this look worth repeating',
  channelSimilarity: '{channel} · similarity {pct}%',
  clipNote: 'CLIP compares the style and content of the image, not the text on it.',

  // format repeatability
  repeatRepeatable: 'format repeats',
  repeatMixed: 'worked for some',
  repeatOneOff: 'looks like a one-off',
  repeatUnknown: 'not enough data',
  repeatNoEmbedding: 'the video has no embedding yet — the background collector will compute it within an hour',
  repeatFewSimilar: 'few similar videos from other channels collected ({count})',
  repeatStats: 'outlier ≥ ×{hitScore} on {hit} of {total} channels with similar videos; median best video per channel ×{median}',
  repeatTitle: 'Does the format repeat?',
  repeatSub: 'semantically similar videos from other channels — who else it worked for',
  similarityPct: 'similarity {pct}%',
  titleOpening: '{count} other channels start their titles the same way ("{opening}…").',
  repeatCaveat: 'Based on your collected data: a "one-off" may just mean the channels that repeated the format are not collected.',

  trajectoryTitle: 'How it gained views',
  trajectorySub: "from collector snapshots; dashed line — this channel's typical video",
  compareOthers: 'Compare with others',

  briefSaved: 'Brief saved to drafts',
  computing: 'Computing…',
  briefFailed: '<b>Could not build the brief.</b><br>{error}',
  briefFailedEmpty: 'the video is not in your database — collect the channel and open the brief again',
};
