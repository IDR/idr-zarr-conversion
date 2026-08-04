"use strict";

// === State ===
let biaContext = null;
let state = null;

// === DOM helpers ===
function $(id) {
  return document.getElementById(id);
}

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function extractUrl(value) {
  if (!value) return "";
  const m = value.match(/https?:\/\/\S+/);
  return m ? m[0].replace(/\.$/, "") : value;
}

function slugify(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

// === ID helpers ===
function nextCounter(list, prefix) {
  return list.length + 1;
}

// === Init ===
async function init() {
  try {
    const res = await fetch("bia_context.json");
    biaContext = await res.json();
  } catch (err) {
    console.error("Failed to load BIA context", err);
    showStatus("Failed to load BIA context: " + err.message, true);
  }

  $("loadBtn").addEventListener("click", loadStudy);
  $("addImageAcquisitionBtn").addEventListener("click", addImageAcquisitionProtocol);
  $("addSpecimenImagingBtn").addEventListener("click", addSpecimenImagingPreparationProtocol);
  $("addBioSampleBtn").addEventListener("click", addBioSample);
  $("applyImageAcquisitionToAllBtn").addEventListener("click", () => assignToAll("imageAcquisitionProtocolId"));
  $("applySpecimenImagingToAllBtn").addEventListener("click", () => assignToAll("specimenImagingPreparationProtocolId"));
  $("applyBioSampleToAllBtn").addEventListener("click", () => assignToAll("bioSampleId"));
  $("downloadJsonBtn").addEventListener("click", downloadJson);
  $("downloadTsvBtn").addEventListener("click", downloadTsv);
  $("verifyBtn").addEventListener("click", verifyCrate);
}

function showStatus(msg, isError) {
  const s = $("loadStatus");
  s.textContent = msg;
  s.style.color = isError ? "#dc2626" : "#6b7280";
}

// === Loading ===
async function loadStudy() {
  const input = $("studyInput").value.trim();
  if (!input) {
    showStatus("Enter an IDR URL or study name", true);
    return;
  }
  showStatus("Loading study...");
  try {
    const res = await fetch("/api/study?url=" + encodeURIComponent(input));
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    populateState(data);
    showStatus("Loaded " + state.studyType + " " + state.container["@id"] + "; loading file list...");
    render();
    await loadFilesForAllDatasets();
    showStatus("Ready.");
  } catch (err) {
    showStatus("Error: " + err.message, true);
    console.error(err);
  }
}

function deriveAccessionId(name) {
  if (!name) return "";
  const slashParts = name.split("/");
  const base = slashParts[0].split("-")[0].toUpperCase();
  if (slashParts.length < 2) return base;
  const post = slashParts[1].trim();
  const letterMatch = post.match(/[A-Za-z](?=[^A-Za-z]*$)/);
  const exp = letterMatch ? letterMatch[0].toUpperCase() : post.slice(-1).toUpperCase();
  return base + exp;
}

function populateState(data) {
  const { type, container, annotations, children } = data;
  const name = container.Name || "";
  const accessionId = deriveAccessionId(name);

  const licenseRaw = annotations.License || "";
  const license = extractUrl(licenseRaw) || "https://creativecommons.org/licenses/by/4.0/";

  const pubDoiRaw = annotations["Publication DOI"] || "";
  const pubDoiUrl = extractUrl(pubDoiRaw);
  const dataDoiRaw = annotations["Data DOI"] || "";
  const dataDoiUrl = extractUrl(dataDoiRaw);
  const pubmedRaw = annotations["PubMed ID"] || "";
  const pubmedId = pubmedRaw.split(/\s+/)[0] || null;

  const organism = annotations.Organism || "";

  state = {
    studyType: type,
    container,
    annotations,
    study: {
      id: "./",
      name,
      description: container.Description || "",
      license,
      datePublished: annotations["Release Date"] || "",
      accessionId,
      keywords: annotations["Study Type"] ? [annotations["Study Type"]] : [],
      imagingMethodName: annotations["Imaging Method"] ? [annotations["Imaging Method"]] : [],
      doi: dataDoiUrl || null,
      pubmedId,
      acknowledgement: null,
      funding: [],
      seeAlso: [],
    },
    authors: buildAuthors(annotations["Publication Authors"] || ""),
    publication: pubDoiUrl
      ? {
          id: pubDoiUrl,
          name: annotations["Publication Title"] || "",
          doi: pubDoiUrl,
          pubmedId,
          authorNames: annotations["Publication Authors"] || "",
        }
      : null,
    datasets: children.map((c) => ({
      id: (type === "screen" ? "#Plate-" : "#Dataset-") + c["@id"],
      rawId: c["@id"],
      name: c.Name || "",
      description: c.Description || "",
      bioSampleId: null,
      imageAcquisitionProtocolId: null,
      specimenImagingPreparationProtocolId: null,
      files: [],
      filesLoaded: false,
    })).map((d) => {
      if (type === "screen") {
        const z = d.name ? d.name + ".ome.zarr" : "";
        d.files = [{ path: z, zarr_name: z }];
        d.filesLoaded = true;
      }
      return d;
    }),
    bioSamples: [],
    imageAcquisitionProtocols: [],
    specimenImagingPreparationProtocols: [],
    nextBioSampleId: 1,
    nextImageAcquisitionId: 1,
    nextSpecimenImagingId: 1,
    nextChannelId: 1,
  };

  if (organism) {
    const bsId = "#biosample-1";
    state.bioSamples.push({
      id: bsId,
      name: organism,
      description: organism,
      organism,
      taxonId: "",
      taxonScientificName: organism,
      taxonCommonName: "",
    });
    state.datasets.forEach((d) => (d.bioSampleId = bsId));
    state.nextBioSampleId = 2;
  } else {
    state.nextBioSampleId = 1;
  }
}

function buildAuthors(authorsStr) {
  if (!authorsStr) return [];
  return authorsStr
    .split(",")
    .map((n, i) => ({
      id: "#author-" + i,
      name: n.trim(),
      address: null,
      website: null,
      memberOf: [],
      role: ["author"],
      email: null,
    }))
    .filter((a) => a.name);
}

// === Render ===
function render() {
  if (!state) return;
  $("studySection").classList.remove("hidden");
  $("protocolsSection").classList.remove("hidden");
  $("bioSamplesSection").classList.remove("hidden");
  $("datasetsSection").classList.remove("hidden");
  $("filesSection").classList.remove("hidden");
  $("exportSection").classList.remove("hidden");

  renderStudy();
  renderProtocols();
  renderBioSamples();
  renderDatasets();
  renderFiles();
  updateApplyButtons();
}

function updateApplyButtons() {
  $("applyImageAcquisitionToAllBtn").disabled = state.imageAcquisitionProtocols.length === 0;
  $("applySpecimenImagingToAllBtn").disabled = state.specimenImagingPreparationProtocols.length === 0;
  $("applyBioSampleToAllBtn").disabled = state.bioSamples.length === 0;
}

// === Study form ===
function renderStudy() {
  const form = $("studyForm");
  form.innerHTML = "";

  function addField(labelText, value, onChange, full) {
    const group = el("div", "form-group" + (full ? " full" : ""));
    const label = el("label", "", labelText);
    const input = el("input", "");
    input.type = "text";
    input.value = value === null || value === undefined ? "" : value;
    input.oninput = (e) => onChange(e.target.value);
    group.appendChild(label);
    group.appendChild(input);
    return group;
  }

  function addArea(labelText, value, onChange, full) {
    const group = el("div", "form-group" + (full ? " full" : ""));
    const label = el("label", "", labelText);
    const input = el("textarea", "");
    input.value = value === null || value === undefined ? "" : value;
    input.oninput = (e) => onChange(e.target.value);
    group.appendChild(label);
    group.appendChild(input);
    return group;
  }

  form.appendChild(addField("Name", state.study.name, (v) => (state.study.name = v)));
  form.appendChild(addField("Accession ID", state.study.accessionId, (v) => (state.study.accessionId = v)));
  form.appendChild(addField("License", state.study.license, (v) => (state.study.license = v)));
  form.appendChild(addField("Date published", state.study.datePublished, (v) => (state.study.datePublished = v)));
  form.appendChild(addField("Data DOI", state.study.doi, (v) => (state.study.doi = v || null)));
  form.appendChild(addField("PubMed ID", state.study.pubmedId, (v) => (state.study.pubmedId = v || null)));
  form.appendChild(addField("Keywords (comma-separated)", state.study.keywords.join(", "), (v) => (state.study.keywords = splitList(v))));
  form.appendChild(addField("Imaging method (comma-separated)", state.study.imagingMethodName.join(", "), (v) => (state.study.imagingMethodName = splitList(v))));
  form.appendChild(addArea("Description", state.study.description, (v) => (state.study.description = v), true));

  // Authors summary
  const authorGroup = el("div", "form-group full");
  const authorLabel = el("label", "", "Authors");
  const authorInput = el("input", "");
  authorInput.type = "text";
  authorInput.value = state.authors.map((a) => a.name).join(", ");
  authorInput.oninput = (e) => {
    state.authors = buildAuthors(e.target.value);
  };
  authorGroup.appendChild(authorLabel);
  authorGroup.appendChild(authorInput);
  form.appendChild(authorGroup);

  // Publication
  const pubGroup = el("div", "form-group full");
  const pubLabel = el("label", "", "Publication DOI");
  const pubInput = el("input", "");
  pubInput.type = "text";
  pubInput.value = state.publication ? state.publication.doi : "";
  pubInput.oninput = (e) => {
    const v = e.target.value;
    if (v) {
      state.publication = state.publication || { id: v, name: "", doi: v, pubmedId: null, authorNames: "" };
      state.publication.id = v;
      state.publication.doi = v;
    } else {
      state.publication = null;
    }
  };
  pubGroup.appendChild(pubLabel);
  pubGroup.appendChild(pubInput);
  form.appendChild(pubGroup);
}

function splitList(s) {
  return s
    .split(",")
    .map((x) => x.trim())
    .filter((x) => x);
}

// === Protocols ===
function addImageAcquisitionProtocol() {
  const n = state.nextImageAcquisitionId++;
  state.imageAcquisitionProtocols.push({
    id: "#image-acquisition-protocol-" + n,
    name: "",
    description: "",
    imagingInstrumentDescription: "",
    imagingMethodName: [],
    fbbiId: [],
  });
  renderProtocols();
  updateApplyButtons();
}

function removeImageAcquisitionProtocol(index) {
  state.imageAcquisitionProtocols.splice(index, 1);
  state.datasets.forEach((d) => {
    if (!state.imageAcquisitionProtocols.find((p) => p.id === d.imageAcquisitionProtocolId)) {
      d.imageAcquisitionProtocolId = null;
    }
  });
  renderProtocols();
  renderDatasets();
  updateApplyButtons();
}

function addSpecimenImagingPreparationProtocol() {
  const n = state.nextSpecimenImagingId++;
  state.specimenImagingPreparationProtocols.push({
    id: "#imaging-preparation-protocol-" + n,
    name: "",
    description: "",
    signalChannels: [],
  });
  renderProtocols();
  updateApplyButtons();
}

function removeSpecimenImagingPreparationProtocol(index) {
  state.specimenImagingPreparationProtocols.splice(index, 1);
  state.datasets.forEach((d) => {
    if (!state.specimenImagingPreparationProtocols.find((p) => p.id === d.specimenImagingPreparationProtocolId)) {
      d.specimenImagingPreparationProtocolId = null;
    }
  });
  renderProtocols();
  renderDatasets();
  updateApplyButtons();
}

function addSignalChannel(protocolIndex) {
  const protocol = state.specimenImagingPreparationProtocols[protocolIndex];
  const n = state.nextChannelId++;
  protocol.signalChannels.push({
    id: "#channel-" + n,
    identifier: "",
    channelContentDescription: "",
  });
  renderProtocols();
}

function removeSignalChannel(protocolIndex, channelIndex) {
  state.specimenImagingPreparationProtocols[protocolIndex].signalChannels.splice(channelIndex, 1);
  renderProtocols();
}

function renderProtocols() {
  renderImageAcquisitionProtocols();
  renderSpecimenImagingPreparationProtocols();
}

function renderImageAcquisitionProtocols() {
  const panel = $("imageAcquisitionPanel");
  panel.innerHTML = "";
  if (state.imageAcquisitionProtocols.length === 0) {
    panel.appendChild(el("p", "small", "No image acquisition protocols. Add one to assign it to datasets."));
  }
  state.imageAcquisitionProtocols.forEach((p, i) => {
    const card = el("div", "card");
    const header = el("div", "card-header");
    header.appendChild(el("h4", "", "Image acquisition protocol " + (i + 1)));
    const delBtn = el("button", "danger", "Remove");
    delBtn.onclick = () => removeImageAcquisitionProtocol(i);
    header.appendChild(delBtn);
    card.appendChild(header);

    card.appendChild(textInput("Name", p.name, (v) => (p.name = v)));
    card.appendChild(textArea("Description", p.description, (v) => (p.description = v)));
    card.appendChild(textInput("Imaging instrument description", p.imagingInstrumentDescription, (v) => (p.imagingInstrumentDescription = v)));
    card.appendChild(textInput("Imaging method (comma-separated)", p.imagingMethodName.join(", "), (v) => (p.imagingMethodName = splitList(v))));
    card.appendChild(textInput("FBbi IDs (comma-separated)", p.fbbiId.join(", "), (v) => (p.fbbiId = splitList(v))));

    panel.appendChild(card);
  });
}

function renderSpecimenImagingPreparationProtocols() {
  const panel = $("specimenImagingPanel");
  panel.innerHTML = "";
  if (state.specimenImagingPreparationProtocols.length === 0) {
    panel.appendChild(el("p", "small", "No specimen imaging preparation protocols. Add one to assign it to datasets."));
  }
  state.specimenImagingPreparationProtocols.forEach((p, pi) => {
    const card = el("div", "card");
    const header = el("div", "card-header");
    header.appendChild(el("h4", "", "Specimen imaging preparation protocol " + (pi + 1)));
    const delBtn = el("button", "danger", "Remove");
    delBtn.onclick = () => removeSpecimenImagingPreparationProtocol(pi);
    header.appendChild(delBtn);
    card.appendChild(header);

    card.appendChild(textInput("Name", p.name, (v) => (p.name = v)));
    card.appendChild(textArea("Description", p.description, (v) => (p.description = v)));

    const scTitle = el("h4", "", "Signal channels");
    card.appendChild(scTitle);
    if (p.signalChannels.length === 0) {
      card.appendChild(el("p", "small", "No signal channels."));
    }
    p.signalChannels.forEach((sc, si) => {
      const scCard = el("div", "panel");
      scCard.appendChild(textInput("Identifier", sc.identifier, (v) => (sc.identifier = v)));
      scCard.appendChild(textInput("Channel content description", sc.channelContentDescription, (v) => (sc.channelContentDescription = v)));
      const rm = el("button", "danger", "Remove signal channel");
      rm.onclick = () => removeSignalChannel(pi, si);
      scCard.appendChild(rm);
      card.appendChild(scCard);
    });

    const addSc = el("button", "secondary", "Add signal channel");
    addSc.onclick = () => addSignalChannel(pi);
    card.appendChild(addSc);

    panel.appendChild(card);
  });
}

function textInput(label, value, onChange, onBlur) {
  const group = el("div", "form-group");
  const lbl = el("label", "", label);
  const input = el("input", "");
  input.type = "text";
  input.value = value === null || value === undefined ? "" : value;
  input.oninput = (e) => onChange(e.target.value);
  if (onBlur) input.onblur = onBlur;
  group.appendChild(lbl);
  group.appendChild(input);
  return group;
}

function textArea(label, value, onChange) {
  const group = el("div", "form-group");
  const lbl = el("label", "", label);
  const input = el("textarea", "");
  input.value = value === null || value === undefined ? "" : value;
  input.oninput = (e) => onChange(e.target.value);
  group.appendChild(lbl);
  group.appendChild(input);
  return group;
}

// === BioSamples ===
function addBioSample() {
  const n = state.nextBioSampleId++;
  state.bioSamples.push({
    id: "#biosample-" + n,
    name: "",
    description: "",
    organism: "",
    taxonId: "",
    taxonScientificName: "",
    taxonCommonName: "",
  });
  renderBioSamples();
  renderDatasets();
  updateApplyButtons();
}

function removeBioSample(index) {
  const removed = state.bioSamples[index];
  state.bioSamples.splice(index, 1);
  state.datasets.forEach((d) => {
    if (d.bioSampleId === removed.id) d.bioSampleId = state.bioSamples[0]?.id || null;
  });
  renderBioSamples();
  renderDatasets();
  updateApplyButtons();
}

async function ncbiLookup(index) {
  const bs = state.bioSamples[index];
  const name = bs.organism;
  if (!name) return;
  const btn = document.querySelector(`[data-ncbi-index="${index}"]`);
  if (btn) {
    btn.textContent = "Looking up...";
    btn.disabled = true;
  }
  try {
    const res = await fetch("/api/ncbi?q=" + encodeURIComponent(name));
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    if (data.taxid) {
      bs.taxonId = "NCBI:txid" + data.taxid;
      bs.taxonScientificName = data.scientificName || name;
      bs.taxonCommonName = data.commonName || "";
    } else {
      alert("NCBI did not find a taxon for: " + name);
    }
  } catch (err) {
    alert("NCBI lookup failed: " + err.message);
  } finally {
    renderBioSamples();
  }
}

function renderBioSamples() {
  const panel = $("bioSamplesPanel");
  panel.innerHTML = "";
  if (state.bioSamples.length === 0) {
    panel.appendChild(el("p", "small", "No BioSamples. Add at least one to assign to datasets."));
  }
  state.bioSamples.forEach((bs, i) => {
    const card = el("div", "card");
    const header = el("div", "card-header");
    header.appendChild(el("h4", "", "BioSample " + (i + 1)));
    const delBtn = el("button", "danger", "Remove");
    delBtn.onclick = () => removeBioSample(i);
    header.appendChild(delBtn);
    card.appendChild(header);

    card.appendChild(textInput("Name", bs.name, (v) => (bs.name = v)));
    card.appendChild(textInput("Description", bs.description, (v) => (bs.description = v)));
    card.appendChild(textInput("Organism", bs.organism, (v) => {
      bs.organism = v;
      if (!bs.name) bs.name = v;
      if (!bs.description) bs.description = v;
      if (!bs.taxonScientificName) bs.taxonScientificName = v;
    }));

    const taxonGroup = el("div", "panel");
    taxonGroup.appendChild(el("h4", "", "Taxon"));
    taxonGroup.appendChild(textInput("NCBI taxon ID", bs.taxonId, (v) => (bs.taxonId = v)));
    taxonGroup.appendChild(textInput("Scientific name", bs.taxonScientificName, (v) => (bs.taxonScientificName = v)));
    taxonGroup.appendChild(textInput("Common name", bs.taxonCommonName, (v) => (bs.taxonCommonName = v)));
    const lookupBtn = el("button", "secondary", "Look up NCBI taxon");
    lookupBtn.setAttribute("data-ncbi-index", i);
    lookupBtn.onclick = () => ncbiLookup(i);
    taxonGroup.appendChild(lookupBtn);
    card.appendChild(taxonGroup);

    panel.appendChild(card);
  });
}

// === Datasets ===
function renderDatasets() {
  const wrapper = $("datasetsTableWrapper");
  wrapper.innerHTML = "";

  const table = el("table", "");
  const thead = el("thead", "");
  thead.innerHTML = `
    <tr>
      <th>Dataset</th>
      <th>Description</th>
      <th>BioSample</th>
      <th>Image acquisition</th>
      <th>Specimen prep</th>
      <th>Files</th>
    </tr>
  `;
  table.appendChild(thead);
  const tbody = el("tbody", "");

  state.datasets.forEach((d) => {
    const tr = el("tr", "");

    const nameTd = el("td", "", d.name);
    tr.appendChild(nameTd);

    const descTd = el("td", "");
    const descInput = el("input", "");
    descInput.type = "text";
    descInput.value = d.description;
    descInput.oninput = (e) => (d.description = e.target.value);
    descTd.appendChild(descInput);
    tr.appendChild(descTd);

    tr.appendChild(makeSelectCell(d, "bioSampleId", state.bioSamples, (p) => p.name || p.id));
    tr.appendChild(makeSelectCell(d, "imageAcquisitionProtocolId", state.imageAcquisitionProtocols, (p) => p.name || p.id));
    tr.appendChild(makeSelectCell(d, "specimenImagingPreparationProtocolId", state.specimenImagingPreparationProtocols, (p) => p.name || p.id));

    const filesTd = el("td", "");
    if (d.filesLoaded) {
      filesTd.textContent = d.files.length + " file(s)";
    } else {
      filesTd.textContent = "Loading...";
    }
    tr.appendChild(filesTd);

    tbody.appendChild(tr);
  });

  table.appendChild(tbody);
  wrapper.appendChild(table);
}

function makeSelectCell(dataset, field, list, labelFn) {
  const td = el("td", "");
  const select = el("select", "");
  const none = el("option", "", "— none —");
  none.value = "";
  select.appendChild(none);
  list.forEach((item) => {
    const opt = el("option", "", labelFn(item));
    opt.value = item.id;
    select.appendChild(opt);
  });
  select.value = dataset[field] || "";
  select.onchange = (e) => {
    dataset[field] = e.target.value || null;
    // Update IDs in case signal channel IDs changed.
    renderDatasets();
  };
  td.appendChild(select);
  return td;
}

function assignToAll(field) {
  let first;
  if (field === "bioSampleId") first = state.bioSamples[0]?.id;
  else if (field === "imageAcquisitionProtocolId") first = state.imageAcquisitionProtocols[0]?.id;
  else if (field === "specimenImagingPreparationProtocolId") first = state.specimenImagingPreparationProtocols[0]?.id;
  if (!first) return;
  state.datasets.forEach((d) => (d[field] = first));
  renderDatasets();
}

// === File list ===
async function loadFilesForDataset(rawId) {
  const ds = state.datasets.find((d) => d.rawId === rawId);
  if (!ds) return;
  if (state.studyType === "screen") return; // screen plate files are generated locally
  ds.filesLoaded = false;
  renderFiles();
  renderDatasets();
  try {
    const res = await fetch(`/api/files?type=${state.studyType}&id=${rawId}&name=${encodeURIComponent(ds.name)}`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    ds.files = data.files;
    ds.filesLoaded = true;
  } catch (err) {
    alert("Failed to load file list: " + err.message);
  } finally {
    ds.filesLoaded = true;
    renderFiles();
    renderDatasets();
  }
}

async function loadFilesForAllDatasets() {
  for (const d of state.datasets) {
    await loadFilesForDataset(d.rawId);
  }
  renderFiles();
  renderDatasets();
}

function renderFiles() {
  const panel = $("filesPanel");
  panel.innerHTML = "";
  let total = 0;
  state.datasets.forEach((d) => {
    if (!d.filesLoaded || d.files.length === 0) return;
    total += d.files.length;
    const group = el("details", "");
    const summary = el("summary", "", d.name + " (" + d.files.length + " file(s))");
    group.appendChild(summary);
    const ul = el("ul", "");
    d.files.forEach((f) => {
      const li = el("li", "", f.path);
      ul.appendChild(li);
    });
    group.appendChild(ul);
    panel.appendChild(group);
  });
  if (total === 0) {
    panel.appendChild(el("p", "small", "No file list entries. If this is a project the file list is still being loaded."));
  }
}

// === Build crate ===
function buildCrate() {
  const graph = [];

  graph.push({
    "@id": "ro-crate-metadata.json",
    "@type": "CreativeWork",
    conformsTo: { "@id": "https://w3id.org/ro/crate/1.1" },
    about: { "@id": "./" },
  });

  const study = {
    "@id": "./",
    "@type": ["Dataset", "bia:Study"],
    name: state.study.name,
    description: state.study.description,
    license: state.study.license,
    datePublished: state.study.datePublished,
    author: state.authors.map((a) => ({ "@id": a.id })),
    keywords: state.study.keywords,
    acknowledgement: state.study.acknowledgement,
    hasPart: [],
    accessionId: state.study.accessionId,
    doi: state.study.doi,
    pubmedId: state.study.pubmedId,
    funding: state.study.funding,
    seeAlso: state.study.seeAlso,
  };

  if (state.study.imagingMethodName && state.study.imagingMethodName.length) {
    study.imagingMethodName = state.study.imagingMethodName;
  }

  if (state.publication) {
    study.relatedPublication = [{ "@id": state.publication.id }];
    graph.push({
      "@id": state.publication.id,
      "@type": "bia:Publication",
      name: state.publication.name,
      doi: state.publication.doi,
      pubmedId: state.publication.pubmedId,
      authorNames: state.publication.authorNames,
    });
  }

  // Authors
  state.authors.forEach((a) => {
    graph.push({
      "@id": a.id,
      "@type": ["Person", "bia:Contributor"],
      name: a.name,
      address: a.address,
      website: a.website,
      memberOf: a.memberOf,
      role: a.role,
      email: a.email,
    });
  });

  // Taxons (deduplicate by id)
  const taxons = {};
  state.bioSamples.forEach((bs) => {
    if (!bs.taxonId) return;
    taxons[bs.taxonId] = {
      "@id": bs.taxonId,
      "@type": "bia:Taxon",
      scientificName: bs.taxonScientificName,
      commonName: bs.taxonCommonName || null,
    };
  });
  Object.values(taxons).forEach((t) => graph.push(t));

  // BioSamples
  state.bioSamples.forEach((bs) => {
    const entity = {
      "@id": bs.id,
      "@type": "bia:BioSample",
      name: bs.name,
      description: bs.description,
      organismClassification: bs.taxonId ? [{ "@id": bs.taxonId }] : [],
    };
    graph.push(entity);
  });

  // Image acquisition protocols
  state.imageAcquisitionProtocols.forEach((p) => {
    graph.push({
      "@id": p.id,
      "@type": ["bia:ImageAcquisitionProtocol"],
      name: p.name,
      description: p.description,
      imagingInstrumentDescription: p.imagingInstrumentDescription,
      imagingMethodName: p.imagingMethodName,
      fbbiId: p.fbbiId,
    });
  });

  // Specimen imaging prep protocols + signal channels
  state.specimenImagingPreparationProtocols.forEach((p) => {
    const scRefs = p.signalChannels.map((sc) => ({ "@id": sc.id }));
    graph.push({
      "@id": p.id,
      "@type": "bia:SpecimenImagingPreparationProtocol",
      name: p.name,
      description: p.description,
      signalChannelInformation: scRefs,
    });
    p.signalChannels.forEach((sc) => {
      graph.push({
        "@id": sc.id,
        "@type": "bia:SignalChannel",
        identifier: sc.identifier,
        channelContentDescription: sc.channelContentDescription,
      });
    });
  });

  // Datasets
  const fileListRows = [];
  state.datasets.forEach((d) => {
    const dsEntity = {
      "@id": d.id,
      "@type": ["Dataset", "bia:Dataset"],
      name: d.name,
      description: d.description,
      associatedBiologicalEntity: d.bioSampleId ? [{ "@id": d.bioSampleId }] : [],
      associatedSpecimenImagingPreparationProtocol: d.specimenImagingPreparationProtocolId
        ? [{ "@id": d.specimenImagingPreparationProtocolId }]
        : [],
      associatedSpecimen: null,
      associatedCreationProcess: null,
      associatedSourceImage: [],
      associatedImageAcquisitionProtocol: d.imageAcquisitionProtocolId
        ? [{ "@id": d.imageAcquisitionProtocolId }]
        : [],
      associatedAnnotationMethod: [],
      associatedImageAnalysisMethod: [],
      associatedImageCorrelationMethod: [],
      associatedProtocol: [],
    };
    graph.push(dsEntity);
    study.hasPart.push({ "@id": d.id });

    d.files.forEach((f) => {
      fileListRows.push({
        file_path: f.path,
        size_in_bytes: "",
        dataset: d.id,
        type: "http://bia/Image",
        image_type: "raw",
      });
    });
  });

  // File list schema and entity
  if (fileListRows.length > 0) {
    graph.push({
      "@id": "_:ts0",
      "@type": ["csvw:Schema"],
      column: [
        { "@id": "_:col0" },
        { "@id": "_:col1" },
        { "@id": "_:col2" },
        { "@id": "_:col3" },
        { "@id": "_:col4" },
      ],
    });
    graph.push({
      "@id": "_:col0",
      "@type": ["csvw:Column"],
      columnName: "file_path",
      propertyUrl: "http://bia/filePath",
    });
    graph.push({
      "@id": "_:col1",
      "@type": ["csvw:Column"],
      columnName: "size_in_bytes",
      propertyUrl: "http://bia/sizeInBytes",
    });
    graph.push({
      "@id": "_:col2",
      "@type": ["csvw:Column"],
      columnName: "dataset",
      propertyUrl: "http://schema.org/isPartOf",
    });
    graph.push({
      "@id": "_:col3",
      "@type": ["csvw:Column"],
      columnName: "type",
      propertyUrl: "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
    });
    graph.push({
      "@id": "_:col4",
      "@type": ["csvw:Column"],
      columnName: "image_type",
      propertyUrl: null,
    });
    graph.push({
      "@id": "file_list.tsv",
      "@type": ["File", "bia:FileList", "csvw:Table"],
      tableSchema: { "@id": "_:ts0" },
    });
    study.hasPart.push({ "@id": "file_list.tsv" });
  }

  graph.push(study);

  return {
    "@context": biaContext,
    "@graph": graph,
  };
}

const PLACEHOLDER_SIZE = "1024";

function buildFileListTsv() {
  const rows = [];
  state.datasets.forEach((d) => {
    d.files.forEach((f) => {
      rows.push([f.path, PLACEHOLDER_SIZE, d.id, "http://bia/Image", "raw"]);
    });
  });
  let tsv = "file_path\tsize_in_bytes\tdataset\ttype\timage_type\n";
  rows.forEach((r) => {
    tsv += r.join("\t") + "\n";
  });
  return tsv;
}

function downloadJson() {
  const crate = buildCrate();
  const blob = new Blob([JSON.stringify(crate, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "ro-crate-metadata.json";
  a.click();
  URL.revokeObjectURL(a.href);
}

function downloadTsv() {
  const tsv = buildFileListTsv();
  const blob = new Blob([tsv], { type: "text/tab-separated-values" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "file_list.tsv";
  a.click();
  URL.revokeObjectURL(a.href);
}

async function verifyCrate() {
  const resultPanel = $("verifyResult");
  resultPanel.classList.remove("hidden");
  resultPanel.innerHTML = "Validating...";
  try {
    const crate = buildCrate();
    const tsv = buildFileListTsv();
    const res = await fetch("/api/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ro_crate_metadata: crate, file_list: tsv }),
    });
    const data = await res.json();
    renderVerifyResult(data);
  } catch (err) {
    resultPanel.textContent = "Validation request failed: " + err.message;
  }
}

function renderVerifyResult(data) {
  const panel = $("verifyResult");
  panel.classList.remove("hidden");
  panel.innerHTML = "";

  if (data.error) {
    panel.appendChild(el("p", "", "Error: " + data.error));
    return;
  }

  const level = data.report && data.report.highest_error_level;
  const isError = level === "CRITICAL" || level === "ERROR";
  const isWarning = level === "WARNING";
  const summaryText = isError
    ? "Validation failed"
    : isWarning
    ? "Valid with warnings"
    : level
    ? "Valid (informational)"
    : "Valid";
  const summaryColor = isError ? "#dc2626" : isWarning ? "#d97706" : "#16a34a";
  const summary = el("p", "", summaryText);
  summary.style.color = summaryColor;
  summary.style.fontWeight = "bold";
  panel.appendChild(summary);

  if (data.report) {
    const report = data.report;
    const levels = ["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"];
    levels.forEach((lvl) => {
      const issues = report.issues[lvl] || [];
      if (issues.length === 0) return;
      const group = el("details", "");
      const summaryEl = el("summary", "", lvl + " (" + issues.length + ")");
      group.appendChild(summaryEl);
      const ul = el("ul", "");
      issues.forEach((issue) => {
        const li = el("li", "", JSON.stringify(issue));
        ul.appendChild(li);
      });
      group.appendChild(ul);
      panel.appendChild(group);
    });
  }

  if (data.stderr) {
    const pre = el("pre", "small", data.stderr);
    panel.appendChild(pre);
  }
}

init();
