# Using Composer

Composer presents a Stacksmith stack as a component diagram backed by its authored YAML or JSON file. The diagram helps explore relationships; the source file remains authoritative.

In Open stack, choose a repository with the searchable dropdown, then choose its stack file. Search matches repository names and descriptions. Results retain Can edit or Read only badges; navigation and filtering do not switch repositories until you select a result.

## Link to a stack

Opening a stack updates the address bar to `#/stacks/owner/repository/path/to/stack.yaml`. Copy that URL to reopen the file on the repository's current default branch. The link also retains the selected workspace view and view-only mode. GitHub sign-in preserves the requested link, and repository access is checked when the stack opens. Local example URLs use `#/example`.

Links identify the stack and view. Draft edits remain in this browser's recovery storage; sharing a URL does not share those edits.

## Choose an inspection context

Composer works before any deployment targets are configured. In **Settings > Workspace**, choose **Stack preview (no deployment)** to inspect the authored stack against repository and platform configuration. Preview excludes deployment runfiles, variable layers, and credentials. Inputs that depend on a deployment can therefore remain unresolved and cause inspection to fail with a diagnostic.

When one configured target grants Infrastructure planning access and no choice has been made, Composer selects it automatically. Choose a target in Workspace to include its deployment runfile, variable layers, and authorized preparation credentials. Your explicit preview preference is remembered for the repository. Workspace distinguishes target-loading failures from an empty list and links authorized users to deployment settings.

The canvas reports when managed connections are pending, inspected, or incomplete because inspection failed. Authored connections remain available during failures. Successful Review and Resolution results show their source and configuration revisions under **Inspection context**. PR creation repeats inspection and records that context in the PR description. Deployment planning remains separate from Composer inspection.

While managed connections first load, the diagram is lightly blurred and shows **Loading managed connections**. Component types also show a loading indicator until the catalog is ready. Later edits show **Updating managed connections** while keeping the previous diagram visible. These indicators clear when inspection completes or fails. Unchanged stack source is available immediately from the opened snapshot; edited source renders independently of inspection.

## View without editing

Switch **Editing** to **View only** in the stack toolbar to pan and zoom the diagram and select components or relationships for inspection. Components, connection labels, and routing points stay fixed. Property edits, component additions and deletions, connection changes, managed rule changes, draft restoration, and undo/redo are disabled. Switch back to **Editing** to resume changes. The URL includes `mode=view` while this mode is active.

## Explore the catalog

The catalog comes from Stacksmith inspection of the configured platform and managed associations. Add a component from an available type, then select it to edit its name and properties. Property controls follow the values already present in the authored stack. Lists and objects can be edited recursively, with a JSON editor available for less common structures.

Composer currently builds its catalog from explicit module mappings. A type that Stacksmith can resolve through a convention may not appear in the Composer catalog yet.

## Connect components

Select a component and use the pencil beside its name to rename its stack identifier. Composer updates static component references, root outputs and operations, and diagram positions and routes. Renaming is an undoable draft change and can cause infrastructure replacement when applied. Expressions with dynamic component lookups must be edited in source before renaming.

Connect a named output on one component to a property on another. Composer records the reference in the stack source and displays it as a relationship on the diagram. Managed associations are shown separately so you can distinguish platform-provided relationships from authored references. Stack-level `disabled_associations` can be edited to opt out of a managed association.

Select a line to open **Connection details** in the sidebar. Managed connections show their platform rule and an **Affected connections** preview. **Disconnect from component** opts the receiving component out of that rule immediately, including any connections where it acts as a producer under the same rule. Other rules and authored references remain enabled. The sidebar stays open with **Undo disconnect**, which restores that opt-out without discarding later edits. Normal undo/redo also remains available. Authored lines instead offer **Remove reference**.

Use **More actions > Disable rule across entire stack…** for a stack-wide opt-out. Composer asks you to confirm its scope and affected connections. Both the sidebar and confirmation mark impact information from the last inspection while the current draft is pending or has failed. Inspection runs again after changes; a failed inspection leaves Undo and Retry available.

The **Managed connections** button above the canvas lists disabled scopes, including component and stack-wide opt-outs, even when no lines are visible. Re-enable a stack-wide rule before reconnecting a component that also opts out. Stack-wide changes require confirmation; re-enabling a component applies immediately. Review shows a compact disabled-scope summary with **Manage connections** to return to these controls. Opt-outs are stored in the corresponding stack or component `disabled_associations` field; authored references and saved routing are preserved.

The inspector checks references and relationships through Stacksmith and checks component cycles. This is an authoring inspection, not full Stacksmith validation or an infrastructure plan. Run the repository's normal validation and planning workflow before applying infrastructure.

## Inspect configuration resolution

Open **Resolution** in the stack toolbar to inspect the current draft against the managed platform config. The effective configuration includes the merged stack and config, resolved inputs, final component properties, and module blocks. Expand a section to inspect its structure.

Switch to **Resolution history** to see source contributions and merge decisions in order, including managed defaults, transforms, renames, and injected inputs. Expand an event to inspect its metadata. Enter a dotted address such as `inputs.region` or `components.api.properties.instance_type` and choose **Explain** to follow its contributing inputs and mappings. Both authored property names and mapped module input names are supported. Select a component on the canvas first to get a shortcut for its properties. **All configuration** clears the query, and **Refresh** retries inspection.

Values and templates are visible, including overridden contributions and any embedded secrets. Composer uses `stacksmith info explain --show-values` and requires a Stacksmith version providing that command. Resolution uses the current draft and selected inspection context. Deployment inspection includes its runfile and variable layers; stack preview uses repository and platform configuration. Configured Python validations and transforms still execute, and module discovery may fetch sources. This inspection does not generate infrastructure files or run a plan.

## Review and download

Open the review panel to see the exact proposed source and unified diff. Composer preserves the original source byte-for-byte when there are no edits. YAML comments and unchanged scalar styles are preserved for supported edits; changed JSON is formatted with two-space indentation.

Saved component positions and routing points are preserved when opening a stack. New components and moved components or routing points snap to the grid while editing. Drag a line label to reposition it; select a line and use Hide label or Show label to control its visibility. Labels stay visible until manually hidden.

Download the stack file to keep changes outside a GitHub workflow. Component positions, routing points, and label positions are saved under `metadata.stacksmith_platform`. Label visibility is local to the UI. Layout edits update the source and diff without rerunning Stacksmith inspection. When inspection fails or is pending, **Download unvalidated stack** remains available once the authored source has been rendered. A pull request requires successful inspection of the current draft and is reinspected by the server.

## Recover a draft

Composer automatically saves components, managed rule settings, and diagram layout in browser storage. The toolbar reports when the draft is saved. These copies include authored values and the original source, remain on this browser after sign-out, and are scoped to the account, repository, file, and branch. They are not synchronized between devices or backed up by the server.

Reopen the stack to restore a saved draft. Automatic restoration requires the same base commit and original source; restored edits are inspected again using the new snapshot. If the base changed, download the saved recovery file and reconcile the edits manually. Restoring or discarding the saved copy allows new edits to be saved. Returning to the original stack state removes its browser recovery copy.

Use **Download recovery file** to keep a portable copy, including when a session or snapshot expires. **Import recovery file** loads that copy into an unchanged stack for restoration. Recovery files contain the original source and structured draft data; they are not deployable YAML or JSON stacks. If browser storage is unavailable or full, Composer reports that saving failed and you can still download a recovery file.

## Supported source boundary

Composer edits ordinary YAML and JSON mappings. Whole-file Jinja templates, YAML aliases and anchors, and custom tags are opened read-only because rewriting them safely is not supported. Unrelated root fields are retained when an editable stack is changed.
