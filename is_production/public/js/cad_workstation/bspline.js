// B-splines (NURBS) as stored on Spline entities: degree, control points, knot vector, optional weights.
// Evaluation by de Boor's algorithm; global interpolation through fit points (chord-length parameters,
// averaged knots — The NURBS Book, A9.1) for the Spline tool. The worker builds the same curve in OCCT.

export function find_span(n, p, u, U) {
	// n = index of the last control point
	if (u >= U[n + 1]) return n;
	if (u <= U[p]) return p;
	let low = p, high = n + 1, mid = (low + high) >> 1;
	while (u < U[mid] || u >= U[mid + 1]) {
		if (u < U[mid]) high = mid;
		else low = mid;
		mid = (low + high) >> 1;
	}
	return mid;
}

export function basis(span, u, p, U) {
	const N = [1], left = [], right = [];
	for (let j = 1; j <= p; j++) {
		left[j] = u - U[span + 1 - j];
		right[j] = U[span + j] - u;
		let saved = 0;
		for (let r = 0; r < j; r++) {
			const temp = N[r] / (right[r + 1] + left[j - r]);
			N[r] = saved + right[r + 1] * temp;
			saved = left[j - r] * temp;
		}
		N[j] = saved;
	}
	return N;
}

export function evaluate(g, u) {
	const p = g.degree, P = g.control_points, U = g.knots, W = g.weights;
	const n = P.length - 1;
	const span = find_span(n, p, u, U);
	const N = basis(span, u, p, U);
	const out = [0, 0, 0];
	let wsum = 0;
	for (let i = 0; i <= p; i++) {
		const k = span - p + i;
		const w = W ? W[k] : 1;
		for (let d = 0; d < 3; d++) out[d] += N[i] * w * P[k][d];
		wsum += N[i] * w;
	}
	return out.map((v) => v / wsum);
}

export function domain(g) {
	return [g.knots[g.degree], g.knots[g.control_points.length]];
}

export function sample(g, segments = null) {
	const [u0, u1] = domain(g);
	const n = segments || Math.min(2048, Math.max(32, g.control_points.length * 16));
	const out = [];
	for (let i = 0; i <= n; i++) out.push(evaluate(g, u0 + ((u1 - u0) * i) / n));
	return out;
}

// Interpolating spline through fit points: returns {degree, control_points, knots, fit_points}.
export function interpolate(points, degree = 3) {
	const n = points.length - 1;
	if (n < 1) throw new Error(__("A spline needs at least two points."));
	const p = Math.min(degree, n);
	const d = [];
	let total = 0;
	for (let k = 1; k <= n; k++) {
		d[k] = Math.hypot(...[0, 1, 2].map((i) => points[k][i] - points[k - 1][i]));
		total += d[k];
	}
	if (total === 0) throw new Error(__("The spline's points are all the same."));
	const u = [0];
	for (let k = 1; k < n; k++) u[k] = u[k - 1] + d[k] / total;
	u[n] = 1;
	const U = [];
	for (let i = 0; i <= p; i++) U.push(0);
	for (let j = 1; j <= n - p; j++) {
		let sum = 0;
		for (let i = j; i <= j + p - 1; i++) sum += u[i];
		U.push(sum / p);
	}
	for (let i = 0; i <= p; i++) U.push(1);
	// (n+1)×(n+1) system A·P = Q
	const A = [];
	for (let k = 0; k <= n; k++) {
		const row = new Array(n + 1).fill(0);
		const span = find_span(n, p, u[k], U);
		const N = basis(span, u[k], p, U);
		for (let i = 0; i <= p; i++) row[span - p + i] = N[i];
		A.push(row);
	}
	const control = solve(A, points);
	return { degree: p, control_points: control, knots: U, fit_points: points.map((q) => [...q]) };
}

// Gaussian elimination with partial pivoting, for 3 right-hand sides at once.
function solve(A, B) {
	const n = A.length;
	const M = A.map((row, i) => [...row, ...B[i].slice(0, 3)]);
	for (let c = 0; c < n; c++) {
		let pivot = c;
		for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[pivot][c])) pivot = r;
		[M[c], M[pivot]] = [M[pivot], M[c]];
		const v = M[c][c];
		if (Math.abs(v) < 1e-14) throw new Error(__("The spline cannot pass through these points."));
		for (let r = 0; r < n; r++) {
			if (r === c) continue;
			const f = M[r][c] / v;
			if (f) for (let k = c; k < n + 3; k++) M[r][k] -= f * M[c][k];
		}
	}
	return M.map((row, i) => [row[n] / row[i], row[n + 1] / row[i], row[n + 2] / row[i]]);
}
