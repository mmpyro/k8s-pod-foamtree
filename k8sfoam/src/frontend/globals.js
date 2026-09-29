// The .jsx files were written as <script type="text/babel"> tags reading React
// from the page, so the bundle keeps giving them the same globals.
import React from "react";
import ReactDOM from "react-dom/client";

window.React = React;
window.ReactDOM = ReactDOM;
