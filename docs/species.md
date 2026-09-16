# Species coverage

Every bird BirdNET can detect, and whether the frame has art for it. Missing
yours? Open a [Missing bird](https://github.com/arnegiacomo/fugleramme/issues/new/choose)
issue or [cut one yourself](adding-artwork.md) - more plates of a listed bird
are welcome too.

<input id="species-search" type="search" placeholder="Scientific or common name" autocomplete="off" style="width: 100%; padding: 0.5em; font-size: 1em;">
<label style="display: block; margin: 0.5em 0;"><input id="species-gaps" type="checkbox"> Only species without art</label>
<p id="species-status">Loading.</p>

<table>
  <thead><tr><th>Scientific name</th><th>Common name</th><th>Art</th></tr></thead>
  <tbody id="species-rows"></tbody>
</table>

<script src="../assets/fuse.min.js"></script>
<script src="../assets/species.js"></script>
