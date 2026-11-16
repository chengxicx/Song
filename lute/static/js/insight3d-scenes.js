/**
 * Scene builders for the 3D insight charts.
 *
 * Every builder returns:
 *   { group, labels, view }
 * and nothing else -- the page owns the renderer, the camera and the
 * lifecycle, so a chart can be torn down without leaking a WebGL context.
 *
 * Units are normalised: the ground plane is roughly [-2, 2] on x/z and
 * heights run 0..1.6, so one camera fits every chart and switching tabs
 * does not require per-chart framing logic.  Each builder still returns
 * its own `view` because cloud shapes differ.
 */

import * as THREE from 'three';

const STATUS_COLORS = {
  0: '#95a5a6',
  1: '#e74c3c',
  2: '#e67e22',
  3: '#f1c40f',
  4: '#2ecc71',
  5: '#3498db',
  98: '#7f8c8d',
  99: '#9b59b6'
};

const STATUS_LABELS = {
  0: 'Unknown',
  1: '1',
  2: '2',
  3: '3',
  4: '4',
  5: '5',
  98: 'Ignored',
  99: 'Well known'
};

function statusColor(status) {
  return new THREE.Color(STATUS_COLORS[status] || '#95a5a6');
}

function recallColor(t) {
  // Low recall reads warm, high recall cool: an alarm ramp, used only for
  // the retention surface where "low" really does mean a problem.
  const clamped = Math.max(0, Math.min(1, t));
  return new THREE.Color().setHSL(0.02 + clamped * 0.32, 0.68, 0.48);
}

function heightColor(t) {
  // Neutral single-hue ramp for the terrain: a quiet reading day is not
  // "bad", so it must not be painted red.
  const clamped = Math.max(0, Math.min(1, t));
  return new THREE.Color().setHSL(0.56, 0.55, 0.28 + clamped * 0.38);
}

function addLights(group) {
  group.add(new THREE.AmbientLight(0xffffff, 0.8));
  const key = new THREE.DirectionalLight(0xffffff, 1.05);
  key.position.set(2.5, 4, 3);
  group.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.35);
  fill.position.set(-3, 2, -2.5);
  group.add(fill);
}

function axisLine(points, color, opacity) {
  const geometry = new THREE.BufferGeometry().setFromPoints(points);
  const material = new THREE.LineBasicMaterial({
    color: new THREE.Color(color),
    transparent: true,
    opacity: opacity === undefined ? 0.45 : opacity
  });
  return new THREE.Line(geometry, material);
}

// ---------------------------------------------------------------------------
// 1. Retention surface
// ---------------------------------------------------------------------------

function buildRetention(data) {
  const group = new THREE.Group();
  addLights(group);

  const cols = data.x_labels.length;
  const rows = data.y_labels.length;
  const heightScale = 1.6;
  // The lattice is 14 x 12; unscaled it is so wide that every height
  // difference turns into a flat sheet. Compressing the ground plane
  // keeps the vertical signal visible.
  const spread = 0.52;

  const xOf = function (xi) {
    return (xi - (cols - 1) / 2) * spread;
  };
  const zOf = function (yi) {
    return (yi - (rows - 1) / 2) * spread;
  };

  // Theory: one predicted retention curve per stability bucket. In 2D
  // this is exactly the family of curves the stats page draws, laid side
  // by side -- which is why the 3D view earns its keep.
  const predicted = {};
  (data.theory || []).forEach(function (cell) {
    predicted[cell[0] + ':' + cell[1]] = cell[2];
  });
  for (let xi = 0; xi < cols; xi += 1) {
    const points = [];
    for (let yi = 0; yi < rows; yi += 1) {
      const value = predicted[xi + ':' + yi];
      if (value === undefined) continue;
      points.push(new THREE.Vector3(xOf(xi), value * heightScale, zOf(yi)));
    }
    if (points.length > 1) group.add(axisLine(points, '#8fa4b8', 0.28));
  }

  // Measured: a column per bucket with enough samples, at its real rate.
  const barGeometry = new THREE.BoxGeometry(0.4, 1, 0.4);
  (data.cells || []).forEach(function (cell) {
    const material = new THREE.MeshLambertMaterial({
      color: recallColor(cell.z),
      transparent: true,
      opacity: 0.95
    });
    const bar = new THREE.Mesh(barGeometry, material);
    const height = Math.max(0.03, cell.z * heightScale);
    bar.scale.y = height;
    bar.position.set(xOf(cell.x), height / 2, zOf(cell.y));
    group.add(bar);
  });

  const halfW = ((cols - 1) / 2) * spread;
  const halfD = ((rows - 1) / 2) * spread;
  group.add(
    axisLine(
      [
        new THREE.Vector3(-halfW, 0, -halfD),
        new THREE.Vector3(halfW, 0, -halfD)
      ],
      '#888888'
    )
  );
  group.add(
    axisLine(
      [
        new THREE.Vector3(-halfW, 0, -halfD),
        new THREE.Vector3(-halfW, 0, halfD)
      ],
      '#888888'
    )
  );

  const labels = [];
  data.x_labels.forEach(function (text, i) {
    if (i % 3 !== 0 && i !== cols - 1) return;
    labels.push({ x: xOf(i), y: -0.06, z: -halfD - 0.22, text: text, kind: 'axis' });
  });
  data.y_labels.forEach(function (text, i) {
    if (i % 3 !== 0 && i !== rows - 1) return;
    labels.push({ x: -halfW - 0.28, y: -0.06, z: zOf(i), text: text, kind: 'axis' });
  });
  // Height guides, so the columns can be read as percentages.
  [0, 0.5, 1].forEach(function (fraction) {
    labels.push({
      x: -halfW - 0.28,
      y: fraction * heightScale,
      z: halfD,
      text: Math.round(fraction * 100) + '%',
      kind: 'axis'
    });
  });

  return {
    group: group,
    labels: labels,
    view: { radius: 7.4, theta: 0.85, phi: 1.02, target: [0, 0.45, 0] }
  };
}

// ---------------------------------------------------------------------------
// 2. Lexicon galaxy
// ---------------------------------------------------------------------------

function buildGalaxy(data) {
  const group = new THREE.Group();

  const days = data.days || [];
  const statuses = data.status || [];
  const tokens = data.tokens || [];
  const count = days.length;

  if (count === 0) {
    return {
      group: group,
      labels: [],
      view: { radius: 6, theta: 0.7, phi: 1.1, target: [0, 0, 0] }
    };
  }

  const maxDay = Math.max(1, data.max_day || 1);
  const maxTokens = Math.max(1, data.max_tokens || 1);
  const logMax = Math.log1p(maxTokens);

  const positions = new Float32Array(count * 3);
  const colors = new Float32Array(count * 3);

  for (let i = 0; i < count; i += 1) {
    const status = statuses[i] === 99 ? 5 : statuses[i];
    const x = (days[i] / maxDay) * 2 - 1;
    const y = ((status - 1) / 4) * 2 - 1;
    const z = (Math.log1p(tokens[i]) / logMax) * 2 - 1;
    positions[i * 3] = x * 2;
    positions[i * 3 + 1] = y * 1.4;
    positions[i * 3 + 2] = z * 1.6;

    const color = statusColor(statuses[i]);
    colors[i * 3] = color.r;
    colors[i * 3 + 1] = color.g;
    colors[i * 3 + 2] = color.b;
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

  // Measure the cloud before drawing it: both the point size and the
  // frame come from its extent, which depends entirely on the data.
  const box = new THREE.Box3().setFromBufferAttribute(
    geometry.getAttribute('position')
  );
  const extent = new THREE.Vector3();
  box.getSize(extent);
  const center = new THREE.Vector3();
  box.getCenter(center);

  group.add(
    new THREE.Points(
      geometry,
      new THREE.PointsMaterial({
        // Sized from the cloud, not a fixed world unit: the same 0.05 is
        // a fat blob on a 2-unit cloud and an invisible speck on a
        // 10-unit one.
        size: Math.max(0.05, extent.length() * 0.016),
        vertexColors: true,
        transparent: true,
        opacity: 0.9,
        sizeAttenuation: true
      })
    )
  );

  const frame = new THREE.LineSegments(
    new THREE.EdgesGeometry(
      new THREE.BoxGeometry(extent.x || 1, extent.y || 1, extent.z || 1)
    ),
    new THREE.LineBasicMaterial({
      color: new THREE.Color('#888888'),
      transparent: true,
      opacity: 0.22
    })
  );
  frame.position.copy(center);
  group.add(frame);

  const labels = [
    {
      x: box.min.x,
      y: box.max.y,
      z: box.min.z,
      text: STATUS_LABELS[5],
      kind: 'axis'
    },
    {
      x: box.min.x,
      y: box.min.y,
      z: box.min.z,
      text: STATUS_LABELS[1],
      kind: 'axis'
    }
  ];

  const radius = Math.max(3.2, extent.length() * 1.15);
  return {
    group: group,
    labels: labels,
    view: {
      radius: radius,
      theta: 0.85,
      phi: 1.12,
      target: [center.x, center.y, center.z]
    }
  };
}

// ---------------------------------------------------------------------------
// 3. Reading terrain
// ---------------------------------------------------------------------------

function buildTerrain(data) {
  const group = new THREE.Group();
  addLights(group);

  const weeks = data.weeks || [];
  if (weeks.length < 2) {
    return {
      group: group,
      labels: [],
      view: { radius: 6, theta: 0.6, phi: 1.0, target: [0, 0, 0] }
    };
  }

  const heightScale = 1.6;

  // Flatten to one chronological series and smooth it. Adjacent days in
  // this 52x7 grid are independent samples, so raw values give a ridge of
  // vertical blades rather than a terrain. A 7-day window is one column
  // wide and does not touch the week-to-week swings; a 4-week mean does,
  // which is what makes the shape a landscape. The caption says so -- the
  // heights are averages, not single-day counts. Exact daily numbers are
  // on /stats/.
  const SMOOTH_HALF_WINDOW = 14;
  const flat = [];
  weeks.forEach(function (week) {
    week.forEach(function (count) {
      flat.push(count || 0);
    });
  });
  const smoothed = flat.map(function (_value, index) {
    let sum = 0;
    let seen = 0;
    for (
      let k = index - SMOOTH_HALF_WINDOW;
      k <= index + SMOOTH_HALF_WINDOW;
      k += 1
    ) {
      if (k < 0 || k >= flat.length) continue;
      sum += flat[k];
      seen += 1;
    }
    return seen ? sum / seen : 0;
  });
  const smoothMax = Math.max(1, Math.max.apply(null, smoothed));

  // Fixed world size regardless of how many weeks there are: the plane
  // used to be `weeks.length` units wide, so a year of history (52 units)
  // overflowed a camera framed for a 4-unit scene.
  const spanX = 4;
  const spanZ = 2.4;
  const cols = weeks.length;
  const rows = 7;
  const sx = spanX / Math.max(1, cols - 1);
  const sz = spanZ / Math.max(1, rows - 1);

  const positions = [];
  const colors = [];
  const indices = [];

  for (let c = 0; c < cols; c += 1) {
    for (let r = 0; r < rows; r += 1) {
      const ratio = (smoothed[c * rows + r] || 0) / smoothMax;
      positions.push(
        (c - (cols - 1) / 2) * sx,
        ratio * heightScale,
        (r - (rows - 1) / 2) * sz
      );
      const color = heightColor(ratio);
      colors.push(color.r, color.g, color.b);
    }
  }

  for (let c = 0; c < cols - 1; c += 1) {
    for (let r = 0; r < rows - 1; r += 1) {
      const a = c * rows + r;
      const b = a + rows;
      indices.push(a, b, a + 1, a + 1, b, b + 1);
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();

  group.add(
    new THREE.Mesh(
      geometry,
      new THREE.MeshLambertMaterial({
        vertexColors: true,
        side: THREE.DoubleSide,
        flatShading: false
      })
    )
  );

  const labels = [
    {
      x: -spanX / 2,
      y: 0,
      z: spanZ / 2 + 0.25,
      text: data.first_date || '',
      kind: 'axis'
    },
    {
      x: spanX / 2,
      y: 0,
      z: spanZ / 2 + 0.25,
      text: data.last_date || '',
      kind: 'axis'
    }
  ];
  (data.weekdays || []).forEach(function (name, index) {
    labels.push({
      x: -spanX / 2 - 0.3,
      y: 0,
      z: (index - (rows - 1) / 2) * sz,
      text: name,
      kind: 'axis'
    });
  });

  return {
    group: group,
    labels: labels,
    view: { radius: 5.4, theta: 0.42, phi: 0.92, target: [0, 0.45, 0] }
  };
}

// ---------------------------------------------------------------------------
// 4. Level towers
// ---------------------------------------------------------------------------

function buildLevels(data) {
  const group = new THREE.Group();
  addLights(group);

  const levels = data.levels || [];
  if (!levels.length) {
    return {
      group: group,
      labels: [],
      view: { radius: 6, theta: 0.6, phi: 1.0, target: [0, 0.6, 0] }
    };
  }

  const bands = data.bands || ['0', '1', '2', '3', '4', '5', '98', '99'];
  const biggest = Math.max(
    1,
    Math.max.apply(
      null,
      levels.map(function (level) {
        return Math.max(level.total, level.studied);
      })
    )
  );
  const heightScale = 1.6;

  // Square-root scaling: a level holds thousands of words while a user
  // knows dozens, so linear heights would render the studied part as a
  // hairline and hide the whole point of the chart. sqrt keeps both
  // readable, and sqrt(a+b) < sqrt(a)+sqrt(b) guarantees the bands still
  // fit inside the level's shell.
  const norm = function (value) {
    return (Math.sqrt(value) / Math.sqrt(biggest)) * heightScale;
  };

  const spacing = 1.15;
  const labels = [];

  levels.forEach(function (level, index) {
    const x = (index - (levels.length - 1) / 2) * spacing;
    const shellHeight = Math.max(0.05, norm(level.total));

    // Clean edges rather than a wireframe: a wireframe material draws the
    // box's triangulation too, which reads as a cage full of diagonals.
    const shellGeometry = new THREE.EdgesGeometry(
      new THREE.BoxGeometry(0.5, shellHeight, 0.5)
    );
    const shell = new THREE.LineSegments(
      shellGeometry,
      new THREE.LineBasicMaterial({
        color: new THREE.Color('#9aa7b4'),
        transparent: true,
        opacity: 0.4
      })
    );
    shell.position.set(x, shellHeight / 2, 0);
    group.add(shell);

    let stacked = 0;
    bands.forEach(function (band) {
      const value = (level.bands && level.bands[band]) || 0;
      if (!value) return;
      const height = norm(value) - norm(0);
      const mesh = new THREE.Mesh(
        new THREE.BoxGeometry(0.44, height, 0.44),
        new THREE.MeshLambertMaterial({
          color: statusColor(parseInt(band, 10)),
          transparent: true,
          opacity: 0.95
        })
      );
      mesh.position.set(x, stacked + height / 2, 0);
      stacked += height;
      group.add(mesh);
    });

    labels.push({ x: x, y: -0.08, z: 0.42, text: level.level, kind: 'axis' });
  });

  return {
    group: group,
    labels: labels,
    view: { radius: 4.8, theta: 0.5, phi: 1.0, target: [0, 0.55, 0] }
  };
}

export const builders = {
  retention: buildRetention,
  galaxy: buildGalaxy,
  terrain: buildTerrain,
  levels: buildLevels
};

export function build(name, data) {
  const fn = builders[name];
  if (!fn) throw new Error('unknown chart: ' + name);
  return fn(data);
}

export { STATUS_LABELS, STATUS_COLORS };
