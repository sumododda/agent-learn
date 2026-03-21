'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/context/AuthContext';
import { Navbar } from '@/components/Navbar';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  getProviders,
  saveProvider,
  updateProvider,
  deleteProvider,
  testProvider,
  getSearchProviderRegistry,
  getSearchProviders,
  saveSearchProvider,
  updateSearchProvider,
  deleteSearchProvider,
  testSearchProvider,
  setDefaultSearchProvider,
} from '@/lib/api';
import type { ProviderDefinition, ProviderConfig } from '@/lib/types';

// ---------------------------------------------------------------------------
// OpenRouter Section (API key + model)
// ---------------------------------------------------------------------------

interface OpenRouterModel {
  id: string;
  name: string;
}

function ModelSearch({
  models,
  loading,
  value,
  onChange,
  disabled,
}: {
  models: OpenRouterModel[];
  loading: boolean;
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Sync display text when value changes externally
  useEffect(() => {
    if (!value) {
      setQuery('');
    } else {
      const m = models.find((m) => m.id === value);
      setQuery(m ? m.name : value);
    }
  }, [value, models]);

  // Close on outside click
  useEffect(() => {
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, []);

  const filtered = query
    ? models.filter(
        (m) =>
          m.name.toLowerCase().includes(query.toLowerCase()) ||
          m.id.toLowerCase().includes(query.toLowerCase())
      )
    : models;
  const shown = filtered.slice(0, 50);

  return (
    <div ref={ref} className="relative space-y-2">
      <Label>Model</Label>
      {loading ? (
        <p className="text-xs text-muted-foreground">Loading models...</p>
      ) : (
        <>
          <Input
            type="text"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
              if (!e.target.value) onChange('');
            }}
            onFocus={() => setOpen(true)}
            placeholder="Search models... (default: openai/gpt-4o-mini)"
            disabled={disabled}
          />
          {open && shown.length > 0 && (
            <div className="absolute z-50 mt-1 w-full max-h-64 overflow-y-auto bg-popover border border-border rounded-lg shadow-lg">
              {shown.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => {
                    onChange(m.id);
                    setQuery(m.name);
                    setOpen(false);
                  }}
                  className={`w-full text-left px-4 py-2 text-sm hover:bg-accent transition-colors ${
                    m.id === value ? 'text-primary' : 'text-foreground'
                  }`}
                >
                  <span className="font-medium">{m.name}</span>
                  <span className="text-muted-foreground ml-2 text-xs">{m.id}</span>
                </button>
              ))}
              {filtered.length > 50 && (
                <p className="px-4 py-2 text-xs text-muted-foreground">
                  {filtered.length - 50} more &mdash; type to narrow
                </p>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function OpenRouterSection({
  config,
  getToken,
  onRefresh,
}: {
  config: ProviderConfig | null;
  getToken: () => Promise<string | null>;
  onRefresh: () => Promise<void>;
}) {
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [models, setModels] = useState<OpenRouterModel[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Fetch models from OpenRouter public API
  useEffect(() => {
    let cancelled = false;
    setModelsLoading(true);
    fetch('https://openrouter.ai/api/v1/models')
      .then((res) => res.json())
      .then((data) => {
        if (cancelled) return;
        const list: OpenRouterModel[] = (data.data || [])
          .filter((m: Record<string, unknown>) => {
            const arch = (m.architecture as Record<string, string[]>) || {};
            return (arch.input_modalities || []).includes('text') && (arch.output_modalities || []).includes('text');
          })
          .map((m: Record<string, unknown>) => ({ id: m.id as string, name: (m.name as string) || (m.id as string) }));
        setModels(list);
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setModelsLoading(false); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    setModel(config?.extra_fields?.model || '');
    setApiKey('');
    setTestResult(null);
    setError(null);
    setSuccessMsg(null);
  }, [config]);

  const isConfigured = !!config;

  async function handleTest() {
    if (!apiKey) return;
    setTesting(true);
    setTestResult(null);
    setError(null);
    try {
      const token = await getToken();
      await testProvider('openrouter', { credentials: { api_key: apiKey }, extra_fields: {} }, token);
      setTestResult({ ok: true, message: 'Connection successful' });
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof Error ? err.message : 'Test failed' });
    } finally {
      setTesting(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const token = await getToken();
      const credentials: Record<string, string> = {};
      if (apiKey) credentials.api_key = apiKey;
      const extra_fields: Record<string, string> = {};
      if (model) extra_fields.model = model;

      if (isConfigured) {
        await updateProvider('openrouter', { credentials: Object.keys(credentials).length ? credentials : undefined, extra_fields }, token);
      } else {
        await saveProvider({ provider: 'openrouter', credentials, extra_fields }, token);
      }
      setSuccessMsg('Saved');
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove() {
    if (!isConfigured) return;
    setSaving(true);
    setError(null);
    try {
      const token = await getToken();
      await deleteProvider('openrouter', token);
      setSuccessMsg('Removed');
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Remove failed');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h2 className="text-lg font-semibold text-foreground">AI Provider</h2>
        <p className="text-xs text-muted-foreground mt-1">Powered by OpenRouter</p>
      </div>

      {isConfigured && (
        <div className="flex items-center gap-2">
          <span className="inline-block w-2 h-2 bg-green-500 rounded-full" />
          <span className="text-xs text-green-500">
            Active{config?.credential_hint ? ` \u2014 ${config.credential_hint}` : ''}
          </span>
        </div>
      )}

      <div className="space-y-2">
        <Label>
          API Key <span className="text-destructive">*</span>
        </Label>
        <Input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={isConfigured ? 'Leave blank to keep existing' : 'sk-or-v1-...'}
          disabled={saving}
        />
      </div>

      <ModelSearch
        models={models}
        loading={modelsLoading}
        value={model}
        onChange={setModel}
        disabled={saving}
      />

      <div className="flex flex-wrap gap-3 pt-1">
        <Button
          variant="outline"
          size="sm"
          onClick={handleTest}
          disabled={testing || saving || !apiKey}
        >
          {testing ? 'Testing...' : 'Test'}
        </Button>
        <Button
          size="sm"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? 'Saving...' : isConfigured ? 'Update' : 'Save'}
        </Button>
        {isConfigured && (
          <Button
            variant="destructive"
            size="sm"
            onClick={handleRemove}
            disabled={saving}
          >
            Remove
          </Button>
        )}
      </div>

      {testResult && (
        <p className={`text-sm ${testResult.ok ? 'text-green-500' : 'text-destructive'}`}>
          {testResult.message}
        </p>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {successMsg && <p className="text-sm text-green-500">{successMsg}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Search Provider Section (dropdown + key)
// ---------------------------------------------------------------------------

function SearchProviderSection({
  registry,
  configs,
  getToken,
  onRefresh,
}: {
  registry: Record<string, ProviderDefinition>;
  configs: ProviderConfig[];
  getToken: () => Promise<string | null>;
  onRefresh: () => Promise<void>;
}) {
  const providerKeys = Object.keys(registry);
  const defaultConfig = configs.find((c) => c.is_default);
  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    if (providerKeys.length === 0) return;
    if (defaultConfig) {
      setSelectedProvider(defaultConfig.provider);
    } else if (!selectedProvider || !providerKeys.includes(selectedProvider)) {
      setSelectedProvider(providerKeys[0]);
    }
  }, [providerKeys, defaultConfig, selectedProvider]);

  useEffect(() => {
    if (!selectedProvider) return;
    const def = registry[selectedProvider];
    if (def) {
      const vals: Record<string, string> = {};
      for (const field of def.fields) vals[field.key] = '';
      setFormValues(vals);
    }
    setTestResult(null);
    setError(null);
    setSuccessMsg(null);
  }, [selectedProvider, registry]);

  const currentDef = selectedProvider ? registry[selectedProvider] : null;
  const currentConfig = configs.find((c) => c.provider === selectedProvider);
  const isConfigured = !!currentConfig;
  const hasFields = currentDef ? currentDef.fields.length > 0 : false;

  async function handleTest() {
    if (!selectedProvider || !currentDef) return;
    setTesting(true);
    setTestResult(null);
    setError(null);
    try {
      const token = await getToken();
      const credentials: Record<string, string> = {};
      for (const field of currentDef.fields) {
        if (formValues[field.key]) credentials[field.key] = formValues[field.key];
      }
      await testSearchProvider(selectedProvider, { credentials, extra_fields: {} }, token);
      setTestResult({ ok: true, message: 'Connection successful' });
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof Error ? err.message : 'Test failed' });
    } finally {
      setTesting(false);
    }
  }

  async function handleSave() {
    if (!selectedProvider || !currentDef) return;
    setSaving(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const token = await getToken();
      const credentials: Record<string, string> = {};
      for (const field of currentDef.fields) {
        if (formValues[field.key]) credentials[field.key] = formValues[field.key];
      }
      if (isConfigured) {
        await updateSearchProvider(selectedProvider, { credentials, extra_fields: {} }, token);
      } else {
        await saveSearchProvider({ provider: selectedProvider, credentials, extra_fields: {} }, token);
      }
      if (!currentConfig?.is_default) {
        await setDefaultSearchProvider(selectedProvider, token);
      }
      setSuccessMsg('Saved');
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove() {
    if (!selectedProvider || !isConfigured) return;
    setSaving(true);
    setError(null);
    try {
      const token = await getToken();
      await deleteSearchProvider(selectedProvider, token);
      setSuccessMsg('Removed');
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Remove failed');
    } finally {
      setSaving(false);
    }
  }

  if (providerKeys.length === 0) return null;

  return (
    <div className="space-y-5">
      <h2 className="text-lg font-semibold text-foreground">Search Provider</h2>

      <div className="space-y-2">
        <Label>Provider</Label>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {providerKeys.map((key) => {
            const configured = configs.find((c) => c.provider === key);
            const isSelected = selectedProvider === key;
            return (
              <button
                key={key}
                type="button"
                onClick={() => setSelectedProvider(key)}
                disabled={saving}
                className={`relative rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                  isSelected
                    ? 'border-primary bg-primary/5 text-foreground'
                    : 'border-border bg-card text-foreground hover:bg-accent'
                } disabled:opacity-50`}
              >
                <span className="font-medium">{registry[key].name}</span>
                {configured && (
                  <span className="absolute top-1.5 right-1.5 inline-block h-1.5 w-1.5 rounded-full bg-green-500" />
                )}
              </button>
            );
          })}
        </div>
      </div>

      {isConfigured && (
        <div className="flex items-center gap-2">
          <span className="inline-block w-2 h-2 bg-green-500 rounded-full" />
          <span className="text-xs text-green-500">
            Active{currentConfig?.credential_hint ? ` \u2014 ${currentConfig.credential_hint}` : ''}
          </span>
        </div>
      )}

      {currentDef && !hasFields && (
        <p className="text-sm text-muted-foreground bg-muted/50 border border-border rounded-lg px-4 py-3">
          No API key required &mdash; works without authentication.
        </p>
      )}

      {currentDef?.fields.map((field) => (
        <div key={field.key} className="space-y-2">
          <Label>
            {field.label}
            {field.required && <span className="text-destructive ml-1">*</span>}
          </Label>
          <Input
            type={field.secret ? 'password' : 'text'}
            value={formValues[field.key] || ''}
            onChange={(e) => setFormValues((p) => ({ ...p, [field.key]: e.target.value }))}
            placeholder={isConfigured && field.secret ? 'Leave blank to keep existing' : field.placeholder || ''}
            disabled={saving}
          />
        </div>
      ))}

      <div className="flex flex-wrap gap-3 pt-1">
        {hasFields && (
          <Button
            variant="outline"
            size="sm"
            onClick={handleTest}
            disabled={testing || saving}
          >
            {testing ? 'Testing...' : 'Test'}
          </Button>
        )}
        <Button
          size="sm"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? 'Saving...' : isConfigured ? 'Update' : 'Save'}
        </Button>
        {isConfigured && (
          <Button
            variant="destructive"
            size="sm"
            onClick={handleRemove}
            disabled={saving}
          >
            Remove
          </Button>
        )}
      </div>

      {testResult && (
        <p className={`text-sm ${testResult.ok ? 'text-green-500' : 'text-destructive'}`}>
          {testResult.message}
        </p>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      {successMsg && <p className="text-sm text-green-500">{successMsg}</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Account Section
// ---------------------------------------------------------------------------

function AccountSection({ getToken }: { getToken: () => Promise<string | null> }) {
  const [email, setEmail] = useState<string | null>(null);

  useEffect(() => {
    async function decodeEmail() {
      const token = await getToken();
      if (!token) return;
      try {
        const base64 = token.split('.')[1];
        if (!base64) return;
        const json = atob(base64.replace(/-/g, '+').replace(/_/g, '/'));
        const payload = JSON.parse(json);
        if (payload.sub) setEmail(payload.sub);
      } catch {
        // ignore decode errors
      }
    }
    decodeEmail();
  }, [getToken]);

  return (
    <div className="space-y-5">
      <h2 className="text-lg font-semibold text-foreground">Account</h2>

      <div className="space-y-2">
        <Label>Email</Label>
        <Input
          type="email"
          value={email || ''}
          disabled
        />
      </div>

      <p className="text-sm text-muted-foreground">
        More account settings coming soon.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main settings page
// ---------------------------------------------------------------------------

export default function SettingsPage() {
  const router = useRouter();
  const { getToken, isSignedIn, isLoaded } = useAuth();

  const [llmConfigs, setLlmConfigs] = useState<ProviderConfig[]>([]);
  const [searchRegistry, setSearchRegistry] = useState<Record<string, ProviderDefinition>>({});
  const [searchConfigs, setSearchConfigs] = useState<ProviderConfig[]>([]);
  const [loadingData, setLoadingData] = useState(true);

  const fetchData = useCallback(async () => {
    const token = await getToken();
    const [cfgs, sReg, sCfgs] = await Promise.all([
      getProviders(token),
      getSearchProviderRegistry(token),
      getSearchProviders(token),
    ]);
    setLlmConfigs(cfgs);
    const searchRegInner = (sReg as Record<string, unknown>).providers as Record<string, ProviderDefinition> | undefined;
    setSearchRegistry(searchRegInner || sReg);
    setSearchConfigs(sCfgs);
    setLoadingData(false);
  }, [getToken]);

  useEffect(() => {
    if (isLoaded && !isSignedIn) {
      router.push('/login');
      return;
    }
    if (isLoaded && isSignedIn) {
      fetchData();
    }
  }, [isLoaded, isSignedIn, router, fetchData]);

  if (!isLoaded || !isSignedIn) {
    return <div className="text-center text-muted-foreground mt-20">Loading...</div>;
  }
  if (loadingData) {
    return <div className="text-center text-muted-foreground mt-20">Loading...</div>;
  }

  const openrouterConfig = llmConfigs.find((c) => c.provider === 'openrouter') || null;

  return (
    <>
      <Navbar />
      <div className="max-w-[720px] mx-auto px-4 py-8">
        <h1 className="text-2xl font-semibold mb-6">Settings</h1>
        <Tabs defaultValue={0}>
          <TabsList>
            <TabsTrigger value={0}>AI Provider</TabsTrigger>
            <TabsTrigger value={1}>Search</TabsTrigger>
            <TabsTrigger value={2}>Account</TabsTrigger>
          </TabsList>
          <TabsContent value={0} className="pt-6">
            <OpenRouterSection config={openrouterConfig} getToken={getToken} onRefresh={fetchData} />
          </TabsContent>
          <TabsContent value={1} className="pt-6">
            <SearchProviderSection registry={searchRegistry} configs={searchConfigs} getToken={getToken} onRefresh={fetchData} />
          </TabsContent>
          <TabsContent value={2} className="pt-6">
            <AccountSection getToken={getToken} />
          </TabsContent>
        </Tabs>
      </div>
    </>
  );
}
