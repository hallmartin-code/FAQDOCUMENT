<!--
The user turn. Assembled by pitchlens.analysis.prompts around the deck's own content
blocks (the native PDF, or one image per slide).

Sections are delimited by `<!-- pitchlens:section NAME -->`. Placeholders use ${name}
and are substituted with str.Template; a literal dollar sign must be written $$.

Blocks are sent in this order: deck_payload, then operator_context (only when the
analyst passed --context), then instruction.

  deck_payload      always sent. ${transcript}
  operator_context  sent only when the analyst passed --context. ${context}
  instruction       always sent, last. ${slide_count} ${source_filename} ${tool_name}
-->

<!-- pitchlens:section deck_payload -->
EXTRACTED DECK TEXT

Text extracted locally from the deck, one block per slide. Quote from this text when you cite a fact — quotes are checked against it character by character, so paraphrasing inside a quote field will cause the claim to be downgraded. Where a slide's text is empty the content is in the image or the original file; describe what you see rather than quoting it.

${transcript}

<!-- pitchlens:section operator_context -->
OPERATOR CONTEXT

Supplied by the analyst requesting this memo. Treat it as background only. It is not part of the deck, so never cite it as a fact and never let it supply a figure, credential, or customer the slides do not show.

${context}

<!-- pitchlens:section instruction -->
Conduct the full assessment on the deck above (${slide_count} slides, source file ${source_filename}) and submit it by calling the `${tool_name}` tool.
