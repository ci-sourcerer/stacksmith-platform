import { SettingsPanel } from "./SettingsLayout";
import { useState, type ReactNode } from "react";
import { Code2, ExternalLink, TriangleAlert } from "lucide-react";
import type { Platform, SourceDetails } from "../types";

function SourceAttribution({ source }: { source: SourceDetails }) {
  return (
    <div className="source-attribution">
      <details className="source-configuration">
        <summary>
          <Code2 size={13} />
          <span>{source.label}</span>
        </summary>
        {source.content != null && <pre>{source.content}</pre>}
        {source.configuration !== undefined && (
          <pre>{JSON.stringify(source.configuration, null, 2)}</pre>
        )}
        {source.detail && <code title={source.detail}>{source.detail}</code>}
        {source.url && (
          <a href={source.url} target="_blank" rel="noreferrer">
            Open source file <ExternalLink size={12} />
          </a>
        )}
      </details>
    </div>
  );
}

function PlatformSection({
  title,
  description,
  summary,
  children,
}: {
  title: string;
  description: string;
  summary?: ReactNode;
  children: ReactNode;
}) {
  return (
    <SettingsPanel title={title} description={description}>
      {summary}
      <div className="platform-card-grid">{children}</div>
    </SettingsPanel>
  );
}

function PlatformCard({
  title,
  meta,
  source,
  children,
}: {
  title: string;
  meta?: string;
  source?: SourceDetails;
  children?: ReactNode;
}) {
  return (
    <article className="platform-component-card">
      <div className="component-summary">
        <div>
          <h4>{title}</h4>
          {meta && <code>{meta}</code>}
        </div>
      </div>
      {source && <SourceAttribution source={source} />}
      {children}
    </article>
  );
}

export function PlatformView({
  platform: configuration,
}: {
  platform: Platform;
}) {
  const [layer, setLayer] = useState(0);
  const platform = configuration.layers?.[layer]?.summary ?? configuration;
  const localState = platform.backend.type === "local";

  return (
    <section className="information-view">
      <PlatformSection
        title="State & tooling"
        description={
          platform.description ||
          "The backend and tools used to build and manage this stack."
        }
        summary={
          <>
            {configuration.layers && configuration.layers.length > 1 && (
              <label className="configuration-layer-picker">
                Authored configuration layer
                <select
                  value={layer}
                  onChange={(event) => setLayer(Number(event.target.value))}
                >
                  {configuration.layers.map((item, index) => (
                    <option key={item.reference} value={index}>
                      {index + 1}. {item.reference}
                    </option>
                  ))}
                </select>
                <small>
                  Layers are applied in order. This view shows each layer before
                  merging.
                </small>
              </label>
            )}
            <div className="configuration-summary">
              <p>
                {platform.scope} Source: <code>{platform.source}</code>
              </p>
              <small className="field-help">
                Credential-like fields in source configuration are redacted.
              </small>
            </div>
          </>
        }
      >
        <PlatformCard
          title="State backend"
          meta={platform.backend.type ?? platform.backend.kind}
          source={platform.backend.source}
        >
          {localState && (
            <div className="state-warning" role="note">
              <TriangleAlert size={16} />
              <span>
                <strong>Local state is not shared.</strong> It stays on this
                machine
                {platform.backend.path && (
                  <>
                    {" at "}
                    <code>{platform.backend.path}</code>
                  </>
                )}
                . Use an approved remote backend for shared or production
                stacks.
              </span>
            </div>
          )}
        </PlatformCard>
        {Object.entries(platform.tools).map(([name, tool]) => (
          <PlatformCard
            key={name}
            title={name}
            meta={tool.version ?? "Platform default"}
            source={tool.source}
          />
        ))}
      </PlatformSection>

      <PlatformSection
        title="Providers & required context"
        description="Provider versions, instances, and required module inputs."
      >
        {platform.providers.map((provider) => (
          <PlatformCard
            key={provider.name}
            title={provider.name}
            meta={provider.version ?? "Platform default"}
            source={provider.source}
          >
            <code className="provider-address">{provider.address}</code>
            {provider.instances.length > 0 && (
              <div className="provider-instances">
                {provider.instances.map((instance) => (
                  <div key={instance.name}>
                    <strong>{instance.name}</strong>
                    <p>
                      {instance.description || "Default provider instance."}
                    </p>
                    <SourceAttribution source={instance.source} />
                  </div>
                ))}
              </div>
            )}
          </PlatformCard>
        ))}
        {!platform.providers.length && (
          <p className="field-help">
            No providers declared in this configuration.
          </p>
        )}
        <PlatformCard title="Required input sets">
          <p>{platform.required_input_sets.join(", ") || "None declared"}</p>
        </PlatformCard>
      </PlatformSection>

      <PlatformSection
        title="Managed component types"
        description="Each component resolves to an administrator-approved OpenTofu module and public interface."
      >
        {platform.components.map((component) => (
          <article className="platform-component-card" key={component.name}>
            <div className="component-summary">
              <div>
                <h4>{component.description || component.name}</h4>
                <code>{component.name}</code>
              </div>
              <div className="tag-list">
                {component.tags.map((tag) => (
                  <span key={tag}>{tag}</span>
                ))}
              </div>
            </div>
            <SourceAttribution source={component.source} />
            <div className="interface-summary">
              <div>
                <strong>Inputs</strong>
                <span>
                  {component.inputs.map(({ name }) => name).join(", ")}
                </span>
              </div>
              <div>
                <strong>Outputs</strong>
                <span>
                  {component.outputs.map(({ name }) => name).join(", ")}
                </span>
              </div>
            </div>
          </article>
        ))}
        {!platform.components.length && (
          <p className="field-help">No explicit module mappings declared.</p>
        )}
      </PlatformSection>

      <PlatformSection
        title="Managed associations"
        description="Rules that connect approved component outputs to inputs."
      >
        {platform.associations.length ? (
          platform.associations.map((rule) => (
            <PlatformCard
              key={rule.name}
              title={rule.name}
              source={rule.source}
            >
              <p>{rule.description || "No description provided."}</p>
            </PlatformCard>
          ))
        ) : (
          <p className="field-help">No managed associations declared.</p>
        )}
      </PlatformSection>

      <PlatformSection
        title="Validation policies"
        description="Configuration checks applied to managed components."
      >
        {platform.policies.length ? (
          platform.policies.map((policy) => (
            <PlatformCard
              key={`${policy.kind}:${policy.name}`}
              title={policy.name}
              source={policy.source}
            >
              <div className="tag-list policy-kind">
                <span>{policy.kind.replaceAll("_", " ")}</span>
              </div>
              <p>{policy.description || "No description provided."}</p>
            </PlatformCard>
          ))
        ) : (
          <p className="field-help">No named validation policies declared.</p>
        )}
      </PlatformSection>

      {platform.has_default_mapping && (
        <PlatformSection
          title="Default module mapping"
          description="A fallback module mapping is configured for otherwise unmapped types."
        >
          <p className="field-help">
            The catalog currently lists explicitly mapped component types only.
          </p>
        </PlatformSection>
      )}
    </section>
  );
}
