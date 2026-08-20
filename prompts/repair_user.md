<!--
The repair turn, sent when a tool payload fails schema validation.

Sections are delimited by `<!-- pitchlens:section NAME -->`. Placeholders use ${name}
and are substituted with str.Template; a literal dollar sign must be written $$.

  tool_result  the errored tool_result block answering the model's tool_use. ${errors}
  instruction  the text turn that follows it. ${attempt} ${max_attempts}
-->

<!-- pitchlens:section tool_result -->
Schema validation failed:

${errors}

<!-- pitchlens:section instruction -->
Your tool call did not validate against the required schema. The errors above are the exact validator messages, each naming the field it came from.

Fix only what the errors require and call the tool again with the corrected assessment. Keep the rest of your analysis exactly as it was — this is a repair, not a rewrite, and changing untouched fields risks introducing new violations.

Common causes worth checking before you resubmit:

- A claim tagged `fact` is missing its `quote` or its `slide_refs`.
- A claim tagged `speculation` carries `slide_refs`; speculation is your judgment where the deck is silent, so it must have none. Use `inference` if the deck does support it.
- The scorecard is missing a category, has an extra one, or is out of order. All eleven must be present in the specified order; a category the deck gives no basis for takes a null score, not a guessed one.
- A required risk category was not rated.

This is repair attempt ${attempt} of ${max_attempts}. If the assessment still does not validate after the final attempt, the run fails and no memo is produced.
